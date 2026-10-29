"""大模型适配器：OpenAI 兼容协议（DeepSeek / 智谱 / 通义 / OpenAI / Ollama 通用）。

两个关键工程决策：
1. **无 key 也能演示**：AI_ENABLED=False 时返回结构化演示流，前端体验完全一致
   （避免评审时因为没配 key 现场翻车，这是可控性的体现）。
2. **流式统一为生成器**：无论底层是 SSE 还是一次性返回，上层都拿到同一套 chunk 事件。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Generator, Iterable

import requests

from ..config import Config

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class LLMResult:
    content: str = ''
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ''
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)


class LLMClient:
    """薄封装：只需 base_url + api_key + model。"""

    def __init__(self) -> None:
        self.base_url = (Config.AI_BASE_URL or '').rstrip('/')
        self.api_key = Config.AI_API_KEY or ''
        self.model = Config.AI_MODEL or 'deepseek-chat'
        self.vision_model = Config.AI_VISION_MODEL or self.model
        self.timeout = Config.AI_TIMEOUT
        self.max_tokens = Config.AI_MAX_TOKENS

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.base_url)

    @property
    def vision_enabled(self) -> bool:
        """是否具备看图能力（有 key 且有视觉模型名）。"""
        return bool(self.enabled and self.vision_model)

    # ---------------- 同步调用（Agent 用） ----------------
    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             tool_choice: str = 'auto', model: str | None = None) -> LLMResult:
        started = time.time()
        if not self.enabled:
            return LLMResult(content='', model='disabled',
                             latency_ms=int((time.time() - started) * 1000))

        payload = {
            'model': model or self.model,
            'messages': messages,
            'temperature': 0.3,
            'max_tokens': self.max_tokens,
        }
        if tools:
            payload['tools'] = tools
            payload['tool_choice'] = tool_choice

        resp = requests.post(
            f'{self.base_url}/chat/completions',
            headers={'Authorization': f'Bearer {self.api_key}',
                     'Content-Type': 'application/json'},
            json=payload, timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        choice = (data.get('choices') or [{}])[0]
        msg = choice.get('message') or {}

        calls = []
        for tc in msg.get('tool_calls') or []:
            fn = tc.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(id=tc.get('id', ''), name=fn.get('name', ''),
                                  arguments=args))

        return LLMResult(
            content=msg.get('content') or '',
            tool_calls=calls,
            model=data.get('model', self.model),
            latency_ms=int((time.time() - started) * 1000),
            usage=data.get('usage') or {},
        )

    # ---------------- 多模态：看图 ----------------
    def vision(self, prompt: str, image_b64_list: list[str],
               model: str | None = None, max_tokens: int = 800) -> LLMResult:
        """把若干张 base64 图片 + 一段提示词发给多模态模型。

        走 OpenAI 兼容的 content 数组格式（GLM-4V / Qwen-VL / GPT-4o 通用）。
        """
        started = time.time()
        if not self.enabled:
            return LLMResult(content='', model='disabled',
                             latency_ms=int((time.time() - started) * 1000))
        if not image_b64_list:
            return LLMResult(content='', model='no-image')

        content: list[dict] = [
            {'type': 'image_url',
             'image_url': {'url': f'data:image/jpeg;base64,{b64}'}}
            for b64 in image_b64_list
        ]
        content.append({'type': 'text', 'text': prompt})

        payload = {
            'model': model or self.vision_model,
            'messages': [{'role': 'user', 'content': content}],
            'temperature': 0.1,
            'max_tokens': max_tokens,
        }
        resp = requests.post(
            f'{self.base_url}/chat/completions',
            headers={'Authorization': f'Bearer {self.api_key}',
                     'Content-Type': 'application/json'},
            json=payload, timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = ((data.get('choices') or [{}])[0].get('message') or {})
        return LLMResult(
            content=msg.get('content') or '',
            model=data.get('model', payload['model']),
            latency_ms=int((time.time() - started) * 1000),
            usage=data.get('usage') or {},
        )

    # ---------------- 流式调用（前端 SSE 用） ----------------
    def stream(self, messages: list[dict], model: str | None = None,
               temperature: float = 0.7) -> Generator[dict, None, None]:
        """产出事件：{type: 'delta'|'done'|'error', ...}"""
        if not self.enabled:
            yield from self._demo_stream(messages)
            return

        payload = {
            'model': model or self.model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': self.max_tokens,
            'stream': True,
        }
        started = time.time()
        try:
            with requests.post(
                f'{self.base_url}/chat/completions',
                headers={'Authorization': f'Bearer {self.api_key}',
                         'Content-Type': 'application/json'},
                json=payload, timeout=self.timeout, stream=True,
            ) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    if raw.startswith('data: '):
                        raw = raw[6:]
                    if raw.strip() in ('[DONE]', 'done'):
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    delta = (chunk.get('choices') or [{}])[0].get('delta') or {}
                    piece = delta.get('content')
                    if piece:
                        yield {'type': 'delta', 'text': piece}
            yield {'type': 'done',
                   'latency_ms': int((time.time() - started) * 1000)}
        except Exception as e:                       # 网络/额度/超时都要优雅降级
            logger.warning('LLM 流式失败: %s', e)
            yield {'type': 'error', 'message': f'AI 服务暂不可用：{e}'}

    # ---------------- 无 key 演示模式 ----------------
    def _demo_stream(self, messages: list[dict]) -> Generator[dict, None, None]:
        prompt = ''
        for m in reversed(messages):
            if m.get('role') == 'user':
                prompt = str(m.get('content') or '')
                break
        demo = (
            f'（演示模式）我理解你想了解「{prompt[:40]}」。\n\n'
            '当前未配置 AI_API_KEY，因此这里是离线示例输出：\n'
            '1. 平台会把你的作品送进多模态审核 Agent；\n'
            '2. Agent 会抽帧、做视觉与文字风险识别、检索校规判例；\n'
            '3. 最终给出「通过 / 转人工 / 拒绝」与可解释依据。\n\n'
            '配置环境变量 AI_API_KEY 后即可获得真实模型输出。'
        )
        for ch in demo:
            yield {'type': 'delta', 'text': ch}
            time.sleep(0.012)
        yield {'type': 'done', 'latency_ms': 800, 'demo': True}


def sse(event: dict) -> str:
    """把事件编码成 SSE 帧。"""
    return f'data: {json.dumps(event, ensure_ascii=False)}\n\n'


def stream_headers() -> dict:
    return {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',          # 关键：关掉 nginx 缓冲，否则流式会被攒包
        'Connection': 'keep-alive',
    }
