"""创作副驾：让 AI 先"看"视频，再写标题/简介/标签。

解决的问题：
    学生投稿时常常说不清自己拍了什么——"我拍了个挺有意思的东西，你帮我起个标题"。
    纯文本助手没法问出内容，只能瞎编。这个模块让用户直接丢一个视频上来，
    由多模态模型看完画面，再据此生成文案。

和审核 Agent 的区别：
    AuditAgent 关心"能不能发"（合规判定）；
    ContentCopilot 关心"怎么发得好"（创作建议）。
    两者共用同一套视觉工具（抽帧 / 帧选择 / VLM 理解），所以成本可控、结果一致。

事件协议（SSE）：
    {'type':'step',   'step': {...}}     # 抽帧 / 看懂画面 / 写文案
    {'type':'delta',  'text': '...'}     # 文案逐字流式输出
    {'type':'result', 'titles': [...], 'intro': '...', 'tags': [...],
                      'scenes': [...], 'risks': [...]}
    {'type':'done'}
"""
from __future__ import annotations

import json
import logging
import os
import time

from . import tools
from .llm import LLMClient

logger = logging.getLogger(__name__)

JSON_MARKER = '---JSON---'


def _step(sid: str, name: str, status: str = 'running', detail: str = '',
          elapsed_ms: int = 0) -> dict:
    return {'type': 'step',
            'step': {'id': sid, 'name': name, 'status': status,
                     'detail': detail, 'elapsed_ms': elapsed_ms}}


def _extract_json(text: str) -> dict | None:
    """从模型输出里抠出 JSON（容忍代码块包裹）。"""
    if not text:
        return None
    raw = text
    if JSON_MARKER in raw:
        raw = raw.split(JSON_MARKER, 1)[1]
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.strip('`')
        if raw.lower().startswith('json'):
            raw = raw[4:]
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start:end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _build_prompt(scene_text: str, ocr_text: str, filename: str,
                  hint: str, tags: list[str]) -> str:
    return f"""你是一位很懂校园内容的创作搭子。用户上传了一个视频，下面是我"看完"后的客观记录：

【画面理解】
{scene_text or '（无画面信息）'}

【画面中的文字】
{ocr_text or '（无）'}

【视频文件名】
{filename}

【AI 初判的内容标签】
{'、'.join(tags) if tags else '（无）'}

【用户的补充说明】
{hint or '（用户没说自己拍了什么，请完全依据画面判断）'}

请完成两件事，严格按下面格式输出：

1. 先用 2~3 句口语化的中文说说你看到了什么、适合往哪个方向做文案（像朋友看完视频后的反馈，不要说"根据画面"这类 AI 腔）。
2. 然后另起一行输出 `{JSON_MARKER}`，紧接着输出一个 JSON 对象，字段如下：

{{
  "titles": ["标题1", "标题2", "标题3"],
  "intro": "80 字左右的作品简介，可直接用于投稿表单",
  "tags": ["标签1", "标签2", "标签3", "标签4", "标签5"],
  "risk_note": "如果画面对投稿有需要注意的地方就写一句话，没有就留空字符串"
}}

要求：
- 三个标题风格要有区分：真诚纪实 / 有梗好传播 / 文艺有质感；
- 标题不超过 20 字，要贴合画面**实际内容**，不要编造画面里没有的东西；
- 简介口语化，不堆形容词；
- 标签控制在 2~6 个字；
- 只输出上述内容，不要额外解释。"""


def describe_and_write(video_path: str, filename: str, hint: str = ''):
    """生成器：读视频 → 写文案。逐事件 yield，供 SSE 推送。"""
    started = time.time()

    # 1. 抽帧
    yield _step('frames', '抽帧分析画面', 'running')
    t0 = time.time()
    res = tools.extract_frames(video_path, max_frames=12)
    frame_paths = [f['path'] for f in res.get('frames') or []]
    elapsed = int((time.time() - t0) * 1000)
    if not frame_paths:
        yield _step('frames', '抽帧分析画面', 'error',
                    f"抽帧失败：{res.get('error') or '视频无法解码'}", elapsed)
        yield {'type': 'error', 'message': '视频无法解析，请换一个文件（支持 mp4/mov）'}
        yield {'type': 'done'}
        return
    yield _step('frames', '抽帧分析画面', 'done',
                f"共 {res.get('total_frames') or 0} 帧，取 {len(frame_paths)} 帧", elapsed)

    # 2. 看懂画面
    yield _step('vision', '理解画面内容', 'running')
    t0 = time.time()
    vlm = tools.vlm_describe(frame_paths, filename, max_frames=3)
    vision_elapsed = int((time.time() - t0) * 1000)
    if vlm.get('ok'):
        scenes = [str(f.get('scene') or '') for f in (vlm.get('frames') or [])]
        scenes = [s for s in dict.fromkeys(scenes) if s]
        detail = '场景：' + ('／'.join(scenes[:2]) if scenes else '已识别')
        if vlm.get('tags'):
            detail += f"｜标签：{'、'.join(vlm['tags'][:3])}"
    else:
        scenes = []
        detail = vlm.get('note') or '多模态不可用，仅按文件名与文字推测'

    # 画面文字（OCR 也作为写文案的素材）
    ocr_text = ''
    try:
        ocr = tools.call_tool('ocr_text', {'frame_paths': frame_paths})
        ocr_text = (ocr.get('text') or '')[:200]
    except Exception:                       # OCR 不该阻塞写文案
        logger.debug('copilot OCR 失败', exc_info=True)
    yield _step('vision', '理解画面内容', 'done', detail, vision_elapsed)

    # 3. 写文案（流式）
    yield _step('write', '生成文案', 'running')
    t0 = time.time()
    scene_text = '\n'.join(
        f"第{f.get('frame')}帧：{f.get('scene') or '?'}"
        + (f"（画面文字：{f.get('text_in_image')}）" if f.get('text_in_image') else '')
        for f in (vlm.get('frames') or []))
    prompt = _build_prompt(scene_text, ocr_text, filename, hint, vlm.get('tags') or [])

    client = LLMClient()
    raw_parts: list[str] = []
    shown_len = 0          # 已经推给前端的可见字符数

    if not client.enabled:
        # 没有 key 时给出可用的降级文案，保证功能不"空"
        fallback = _fallback_copy(filename, scenes, vlm.get('tags') or [])
        for ch in fallback['chat']:
            yield {'type': 'delta', 'text': ch}
        yield _step('write', '生成文案', 'done', '演示模式（未配置 AI_API_KEY）',
                    int((time.time() - t0) * 1000))
        yield {'type': 'result', **fallback['result'],
               'scenes': scenes, 'risks': vlm.get('risks') or []}
        yield {'type': 'done'}
        return

    try:
        for ev in client.stream(
            [{'role': 'system',
              'content': '你是校园短视频平台的创作助手，中文输出，语气自然不油腻。'},
             {'role': 'user', 'content': prompt}],
            temperature=0.7,
        ):
            if ev.get('type') != 'delta':
                continue
            piece = ev.get('text') or ''
            if not piece:
                continue
            raw_parts.append(piece)
            full = ''.join(raw_parts)

            # 分隔符之后是给程序解析的 JSON，不展示给用户。
            # 分隔符可能被切成两半（"-" / "--" / "---"），
            # 所以要把"可能是分隔符前缀"的尾巴先扣住不发。
            if JSON_MARKER in full:
                visible = full.split(JSON_MARKER, 1)[0]
            else:
                visible = full
                for cut in range(min(len(JSON_MARKER) - 1, len(full)), 0, -1):
                    if JSON_MARKER.startswith(full[-cut:]):
                        visible = full[:-cut]
                        break

            if len(visible) > shown_len:
                yield {'type': 'delta', 'text': visible[shown_len:]}
                shown_len = len(visible)
    except Exception as e:
        logger.warning('copilot 文案生成失败: %s', e)
        # 流式失败 → 退回降级文案，避免用户白等
        if not shown_len:
            fallback = _fallback_copy(filename, scenes, vlm.get('tags') or [])
            for ch in fallback['chat']:
                yield {'type': 'delta', 'text': ch}
            yield _step('write', '生成文案', 'done',
                        f'生成失败，已用降级文案（{str(e)[:40]}）',
                        int((time.time() - t0) * 1000))
            yield {'type': 'result', **fallback['result'],
                   'scenes': scenes, 'risks': vlm.get('risks') or []}
            yield {'type': 'done'}
            return

    full = ''.join(raw_parts)
    parsed = _extract_json(full)
    write_elapsed = int((time.time() - t0) * 1000)
    if parsed:
        titles = [str(t) for t in (parsed.get('titles') or []) if str(t).strip()][:3]
        intro = str(parsed.get('intro') or '').strip()
        tags = [str(t) for t in (parsed.get('tags') or []) if str(t).strip()][:6]
        risk_note = str(parsed.get('risk_note') or '').strip()
        yield _step('write', '生成文案', 'done',
                    f'已生成 {len(titles)} 个标题 / 简介 {len(intro)} 字 / '
                    f'{len(tags)} 个标签', write_elapsed)
        yield {'type': 'result', 'titles': titles, 'intro': intro, 'tags': tags,
               'risk_note': risk_note, 'scenes': scenes,
               'risks': vlm.get('risks') or [],
               'elapsed_ms': int((time.time() - started) * 1000)}
    else:
        # 模型没按格式输出：保留流式文本，至少让用户能复制
        yield _step('write', '生成文案', 'done',
                    '已生成（未解析出结构化字段，可直接复制上面的文本）',
                    write_elapsed)
        yield {'type': 'result', 'titles': [],
               'intro': ''.join(raw_parts).split(JSON_MARKER)[0].strip()[:400],
               'tags': vlm.get('tags') or [], 'risk_note': '',
               'scenes': scenes, 'risks': vlm.get('risks') or [],
               'elapsed_ms': int((time.time() - started) * 1000)}
    yield {'type': 'done'}


def _fallback_copy(filename: str, scenes: list[str], tags: list[str]) -> dict:
    """无 AI 时的模板文案：保证功能可用，并明确标注这是演示。"""
    base = os.path.splitext(filename)[0]
    scene = scenes[0] if scenes else '校园日常'
    tag_pool = tags or ['校园生活', '日常记录', 'Vlog']
    chat = (f"我看了一下《{base}》，画面主要是{scene}。"
            f"（当前未配置 AI_API_KEY，以下是模板文案，配置后会自动换成真正的内容理解）")
    return {
        'chat': chat,
        'result': {
            'titles': [f'{base}：{scene}的一天',
                       f'记录一下：{scene}',
                       f'{scene}｜校园切片'],
            'intro': f'这是一条关于{scene}的校园记录，来自{base}。',
            'tags': tag_pool[:5],
            'risk_note': '',
        },
    }
