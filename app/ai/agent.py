"""审核 Agent：规划 → 调用工具 → RAG 取证 → 决策 → 可解释报告。

为什么"手写编排"而不是挂个 LangChain AgentExecutor：
- 链路只有 6 个工具、状态机是线性的，手写能精确控制**每一步的耗时可观测**与
  **失败降级点**（评审现场网络抖动不能整条链路崩掉）；
- 每一步都产出结构化事件，前端可以渲染成"推理时间线"，这是交互创新的落点。

事件协议（SSE 推给前端）：
    {type:'step',   step:{id,name,status,detail,...}}
    {type:'token',  text:'...'}            # LLM 流式解说
    {type:'result', verdict, score, ...}   # 最终结论
    {type:'error',  message}
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Generator

from ..config import Config
from . import tools
from .llm import LLMClient
from .rag import evidence_text

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是「校园内容共治」平台的审核 Agent，负责判断学生投稿视频是否合规。

工作要求：
1. 基于给定证据（视觉打分 / OCR 文字 / 文件名 / 检索到的规范条款）做判断，不得臆测；
2. 必须引用具体条款编号（如 R2-1）作为依据；没有匹配条款时明确说明"按通用社区规范"；
3. 对"擦边但合规"的内容要敢于放行（如成人礼、体育竞技、实验演示），误伤同样是错误；
4. 输出严格 JSON，不要 markdown 代码块，字段如下：
{
  "verdict": "pass|manual|reject",
  "score": 0-100,
  "confidence": 0-1,
  "categories": ["色情低俗"],
  "reasons": ["具体理由1", "具体理由2"],
  "evidence": [{"type":"rule|frame|text|meta","ref":"R2-1","detail":"说明"}],
  "suggestions": ["给作者的整改建议"]
}

判定口径：score>=75 且无高危类别 → pass；45~74 或证据冲突 → manual；<45 或命中高危 → reject。"""


@dataclass
class Step:
    id: str
    name: str
    status: str = 'pending'      # pending|running|done|skipped|error
    detail: str = ''
    elapsed_ms: int = 0
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {'id': self.id, 'name': self.name, 'status': self.status,
                'detail': self.detail, 'elapsed_ms': self.elapsed_ms,
                'data': self.data}


class AuditAgent:
    """一次审核 = 一条可观测的执行链。"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient()

    # ---------- 主入口：产出事件流 ----------
    def run(self, *, video_path: str, filename: str, frames: list[str] | None = None,
            ocr_hint: str = '', with_llm: bool = True) -> Generator[dict, None, None]:
        started = time.time()
        steps = [
            Step('meta', '检查元信息'),
            Step('frames', '抽取关键帧'),
            Step('vision', '画面理解'),
            Step('text', '画面文字识别'),
            Step('rules', '检索规范与判例'),
            Step('decide', '综合裁定'),
        ]

        def emit_step(step: Step):
            return {'type': 'step', 'step': step.as_dict()}

        # 1. 元信息
        steps[0].status = 'running'
        yield emit_step(steps[0])
        t0 = time.time()
        meta = tools.call_tool('meta_risk', {'filename': filename})
        steps[0].status = 'done'
        steps[0].elapsed_ms = int((time.time() - t0) * 1000)
        steps[0].detail = f"命中可疑词：{'、'.join(meta.get('flags') or [])}" \
            if meta.get('flags') else '文件名无异常'
        steps[0].data = meta
        yield emit_step(steps[0])

        # 2. 抽帧（调用方已提供则复用，避免重复解码）
        steps[1].status = 'running'
        yield emit_step(steps[1])
        t0 = time.time()
        if frames:
            frame_paths = frames
            total = len(frames)
            frames_note = f'复用已抽取的 {total} 帧'
        else:
            res = tools.call_tool('extract_frames', {'video_path': video_path})
            frame_paths = [f['path'] for f in res.get('frames') or []]
            total = res.get('total_frames') or 0
            frames_note = f"共 {total} 帧，抽取 {len(frame_paths)} 帧" \
                if frame_paths else f"抽帧失败：{res.get('error')}"
        steps[1].status = 'done' if frame_paths else 'error'
        steps[1].elapsed_ms = int((time.time() - t0) * 1000)
        steps[1].detail = frames_note
        steps[1].data = {'count': len(frame_paths), 'video_frames': total}
        yield emit_step(steps[1])

        # 3. 视觉：优先多模态理解（VLM），不可用时降级 CLIP；两路都失败则记录原因
        steps[2].status = 'running'
        yield emit_step(steps[2])
        t0 = time.time()
        vlm = tools.call_tool('vlm_describe', {
            'frame_paths': frame_paths, 'filename': filename, 'max_frames': 3})
        clip = {'available': False, 'scores': {}, 'labels': []}
        if not vlm.get('ok'):
            clip = tools.call_tool('clip_risk', {'frame_paths': frame_paths})
        # 合并成一个 vision 视图，下游判定表统一消费
        vision = dict(clip)
        vision['vlm'] = vlm
        vision['available'] = bool(clip.get('available') or vlm.get('ok'))
        steps[2].status = 'done'
        steps[2].elapsed_ms = int((time.time() - t0) * 1000)

        if vlm.get('ok'):
            cats = vlm.get('categories') or []
            scenes = [str(f.get('scene') or '') for f in (vlm.get('frames') or [])]
            scenes = [s for s in dict.fromkeys(scenes) if s][:2]
            steps[2].detail = (
                f"理解 {len(vlm.get('frames') or [])} 帧"
                + (f"（{' / '.join(scenes)}）" if scenes else '')
                + (f"，发现风险：{'、'.join(cats)}" if cats else '，未发现风险信号')
            )
        elif clip.get('available'):
            scores = clip.get('scores') or {}
            top = max(scores.items(), key=lambda kv: kv[1]) if scores else None
            steps[2].detail = (f'VLM 不可用，退回 CLIP：最高 {top[0]} = {top[1]:.2f}'
                               if top else 'CLIP 无有效分数')
        else:
            steps[2].detail = vlm.get('note') or clip.get('note') or '视觉模型不可用'
        steps[2].data = {
            'mode': 'vlm' if vlm.get('ok') else ('clip' if clip.get('available') else 'none'),
            'categories': vlm.get('categories') or [],
            'risks': (vlm.get('risks') or [])[:5],
            'tags': vlm.get('tags') or [],
            'frames': [{'frame': f.get('frame'), 'scene': f.get('scene')}
                       for f in (vlm.get('frames') or [])],
            'scores': clip.get('scores') or {},
        }
        yield emit_step(steps[2])

        # 4. 文字
        steps[3].status = 'running'
        yield emit_step(steps[3])
        t0 = time.time()
        ocr = {'text': ocr_hint, 'available': bool(ocr_hint)} if ocr_hint \
            else tools.call_tool('ocr_text', {'frame_paths': frame_paths})
        steps[3].status = 'done'
        steps[3].elapsed_ms = int((time.time() - t0) * 1000)
        steps[3].detail = (f'识别到 {len(ocr.get("text") or "")} 字' if ocr.get('text')
                           else ocr.get('note') or '画面无文字')
        steps[3].data = {'text': (ocr.get('text') or '')[:500],
                         'available': ocr.get('available')}
        yield emit_step(steps[3])

        # 5. RAG
        steps[4].status = 'running'
        yield emit_step(steps[4])
        t0 = time.time()
        query = ' '.join(filter(None, [
            filename,
            ocr.get('text') or '',
            ' '.join(k for k in (vision.get('scores') or {}).keys()),
        ]))
        kw = tools.call_tool('keyword_risk', {'texts': [filename, ocr.get('text') or '']})
        hits = tools.call_tool('rule_lookup', {'query': query or filename, 'top_k': 4})
        steps[4].status = 'done'
        steps[4].elapsed_ms = int((time.time() - t0) * 1000)
        steps[4].detail = (f"命中 {len(hits.get('hits') or [])} 条规范/判例"
                           if hits.get('hits') else '未命中直接条款')
        steps[4].data = hits
        yield emit_step(steps[4])

        # 6. 决策
        steps[5].status = 'running'
        yield emit_step(steps[5])
        t0 = time.time()
        verdict_payload = None

        if with_llm and self.llm.enabled:
            user_prompt = self._build_prompt(
                filename=filename, meta=meta, vision=vision, ocr=ocr,
                kw=kw, hits=hits, frames=len(frame_paths))
            yield {'type': 'token', 'text': ''}
            for ev in self.llm.stream([
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': user_prompt},
            ], temperature=0.2):
                if ev['type'] == 'delta':
                    # 决策阶段的 LLM 输出是 JSON，不逐字推给前端（会闪现半截 JSON）
                    continue
                if ev['type'] == 'error':
                    yield {'type': 'error', 'message': ev.get('message', 'AI 调用失败')}
            try:
                res = self.llm.chat([
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_prompt},
                ])
                verdict_payload = self._parse_json(res.content)
                steps[5].detail = f'LLM 裁定（{res.latency_ms}ms）'
            except Exception as e:
                logger.warning('LLM 裁定失败，走规则兜底: %s', e)
                steps[5].detail = f'LLM 不可用，规则兜底（{e}）'
        else:
            steps[5].detail = '未配置 LLM，使用规则引擎裁定'

        if not verdict_payload:
            verdict_payload = self._rule_verdict(meta, vision, ocr, kw, hits)

        # ---- fail-closed 总闸（无论走 LLM 还是规则，都必须经过这一关）----
        # 之前只有规则路径会算 evidence_strength 并做零证据拦截，
        # 而 LLM 裁定的 JSON 里没有这个字段 —— 等于"零证据 + LLM 说 pass"
        # 会被直接放行，是个安全漏洞。这里统一补算并强制降级。
        has_vision = bool((vision.get('vlm') or {}).get('ok') or vision.get('available'))
        has_ocr = bool((ocr.get('text') or '').strip())
        has_kw = bool(kw.get('hits') or {})
        evidence_strength = sum([has_vision, has_ocr, has_kw])
        verdict_payload.setdefault('evidence_strength', evidence_strength)

        if evidence_strength == 0 and verdict_payload.get('verdict') == 'pass':
            verdict_payload['verdict'] = 'manual'
            try:
                verdict_payload['score'] = min(int(verdict_payload.get('score') or 60), 60)
            except (TypeError, ValueError):
                verdict_payload['score'] = 60
            try:
                verdict_payload['confidence'] = min(
                    float(verdict_payload.get('confidence') or 0.3), 0.2)
            except (TypeError, ValueError):
                verdict_payload['confidence'] = 0.2
            reasons = list(verdict_payload.get('reasons') or [])
            reasons.insert(0, '⚠️ 视觉与文字证据均不可用，为避免漏判，本次不自动通过，已转人工复核')
            verdict_payload['reasons'] = reasons
            steps[5].detail = (steps[5].detail or '') + '｜零证据已强制转人工'

        steps[5].status = 'done'
        steps[5].elapsed_ms = int((time.time() - t0) * 1000)
        steps[5].data = {k: verdict_payload[k] for k in
                         ('verdict', 'score', 'confidence') if k in verdict_payload}
        yield emit_step(steps[5])

        yield {
            'type': 'result',
            'verdict': verdict_payload.get('verdict', 'manual'),
            'score': verdict_payload.get('score', 60),
            'confidence': verdict_payload.get('confidence', 0.5),
            'categories': verdict_payload.get('categories', []),
            'reasons': verdict_payload.get('reasons', []),
            'evidence': verdict_payload.get('evidence', []),
            'suggestions': verdict_payload.get('suggestions', []),
            'evidence_strength': verdict_payload.get('evidence_strength'),
            'steps': [s.as_dict() for s in steps],
            'elapsed_ms': int((time.time() - started) * 1000),
            'model': self.llm.model if self.llm.enabled else 'rule-engine',
            'ai_enabled': self.llm.enabled,
        }
        yield {'type': 'done'}

    # ---------- Prompt 构造 ----------
    def _build_prompt(self, *, filename, meta, vision, ocr, kw, hits, frames) -> str:
        scores = vision.get('scores') or {}
        score_txt = ('、'.join(f'{k}={v:.2f}' for k, v in scores.items())
                     if scores else '未使用 CLIP')
        # 多模态理解结果（主证据）
        vlm = vision.get('vlm') or {}
        if vlm.get('ok'):
            scene_lines = []
            for f in (vlm.get('frames') or []):
                scene_lines.append(f"  第{f.get('frame')}帧：{f.get('scene') or '?'}"
                                   + (f"（画面文字：{f.get('text_in_image')}）"
                                      if f.get('text_in_image') else ''))
            risk_lines = [
                f"  - 第{r.get('frame')}帧 [{r.get('severity')}] {r.get('type')}：{r.get('detail')}"
                for r in (vlm.get('risks') or [])] or ['  （未发现风险）']
            vlm_txt = ('场景理解：\n' + '\n'.join(scene_lines) +
                       '\n多模态发现的风险：\n' + '\n'.join(risk_lines) +
                       f"\n内容标签：{'、'.join(vlm.get('tags') or []) or '无'}")
        else:
            vlm_txt = f"（多模态不可用：{vlm.get('note') or '未启用'}）"

        rules_txt = '\n'.join(
            f"- [{h['id']}]《{h['title']}》：{h['text']}"
            for h in (hits.get('hits') or [])) or '（无命中条款）'
        return f"""待审投稿信息：
- 文件名：{filename}
- 关键帧数：{frames}
- 文件名可疑信号：{meta.get('flags') or '无'}
{vlm_txt}
- CLIP 视觉打分：{score_txt}
- 画面 OCR 文字：{ocr.get('text') or '（无）'}
- 关键词命中：{json.dumps(kw.get('hits') or {}, ensure_ascii=False)}
- 相关规范/判例：
{rules_txt}

请按系统要求输出严格 JSON。"""

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = (text or '').strip()
        if text.startswith('```'):
            text = text.strip('`')
            text = text.split('\n', 1)[-1] if '\n' in text else text
        start, end = text.find('{'), text.rfind('}')
        if start >= 0 and end > start:
            text = text[start:end + 1]
        try:
            data = json.loads(text)
            if 'verdict' not in data:
                raise ValueError('缺少 verdict')
            data.setdefault('score', 60)
            data.setdefault('confidence', 0.6)
            data.setdefault('categories', [])
            data.setdefault('reasons', [])
            data.setdefault('evidence', [])
            data.setdefault('suggestions', [])
            return data
        except Exception as e:
            raise ValueError(f'JSON 解析失败: {e}')

    # ---------- 规则兜底（无 LLM / LLM 失败时） ----------
    @staticmethod
    def _rule_verdict(meta, vision, ocr, kw, hits) -> dict:
        score = 100
        reasons: list[str] = []
        categories: list[str] = []
        evidence: list[dict] = []

        scores = vision.get('scores') or {}
        porn = scores.get('porn', 0)
        viol = scores.get('violence', 0)
        weapon = scores.get('weapon', 0)
        addict = scores.get('addiction', 0)
        # 高风险硬否决标记：命中「确定性很高」的信号时，不能靠"扣分后还剩下 50 分"
        # 落到人工区间，那样等于把明显违规内容放给人工兜底，违反最小风险原则。
        hard_block = False

        if porn > 0.6:
            # 相似度越高扣得越多：0.6 时扣 45，0.85 以上直接判定为硬否决
            penalty = 45 + int((porn - 0.6) * 180)
            score -= penalty
            categories.append('色情低俗')
            reasons.append(f'视觉模型判定色情相似度 {porn:.2f}，超过阈值 0.60')
            evidence.append({'type': 'frame', 'ref': f'porn={porn:.2f}',
                             'detail': '关键帧视觉相似度'})
            if porn >= 0.8:
                hard_block = True
        if viol > 0.6:
            score -= 40 + int((viol - 0.6) * 120)
            categories.append('暴力危险')
            reasons.append(f'暴力相似度 {viol:.2f}，超过阈值 0.60')
            if viol >= 0.85:
                hard_block = True
        if weapon > 0.6:
            score -= 35
            categories.append('暴力危险')
            reasons.append(f'武器相似度 {weapon:.2f}')
        if addict > 0.65:
            score -= 25
            categories.append('违法违规')
            reasons.append(f'成瘾物相似度 {addict:.2f}')

        # ---- 多模态理解（VLM）给出的风险证据 ----
        # VLM 是主路径：它能说出"第 2 帧：食堂后厨明火旁堆纸箱"这种可引用依据，
        # 比 CLIP 的相似度分数更可信，所以权重更高。
        vlm = vision.get('vlm') or {}
        if vlm.get('ok'):
            sev_penalty = {'high': 40, 'medium': 20, 'low': 10}
            for risk in (vlm.get('risks') or [])[:6]:
                cat = str(risk.get('type') or '')
                sev = str(risk.get('severity') or 'low').lower()
                if not cat:
                    continue
                score -= sev_penalty.get(sev, 10)
                if cat not in categories:
                    categories.append(cat)
                reasons.append(
                    f"第 {risk.get('frame')} 帧判定「{cat}」（{sev}）："
                    f"{str(risk.get('detail') or '')[:60]}")
                evidence.append({'type': 'frame', 'ref': f"frame#{risk.get('frame')}",
                                 'detail': f"{cat}：{str(risk.get('detail') or '')[:50]}"})
                if sev == 'high':
                    hard_block = True
            tags = vlm.get('tags') or []
            if tags:
                reasons.append(f'画面内容标签：{"、".join(tags[:5])}')
            vlm_text = (vlm.get('text') or '').strip()
            if vlm_text:
                # VLM 读到的画面文字也参与关键词交叉验证
                extra_kw = tools.keyword_risk(vlm_text)
                for cat, words in (extra_kw.get('hits') or {}).items():
                    if cat not in categories:
                        categories.append(cat)
                    score -= min(15, 6 * len(words))
                    reasons.append(f'画面文字命中「{cat}」：{"、".join(words[:3])}')

        kw_hits = kw.get('hits') or {}
        for cat, words in kw_hits.items():
            score -= min(20, 8 * len(words))
            if cat not in categories:
                categories.append(cat)
            reasons.append(f'文本命中「{cat}」相关词：{"、".join(words[:3])}')

        flags = meta.get('flags') or []
        if flags:
            score -= 8
            reasons.append(f'文件名含可疑信号：{"、".join(flags)}')

        for h in (hits.get('hits') or [])[:2]:
            evidence.append({'type': 'rule', 'ref': h['id'],
                             'detail': f"《{h['title']}》：{h['text'][:60]}"})

        ocr_text = ocr.get('text') or ''
        if ocr_text:
            reasons.append(f'画面文字已识别 {len(ocr_text)} 字并参与判定')

        score = max(0, min(100, score))
        high_risk = {'政治敏感', '色情低俗', '违法违规'}

        # ---- 证据充分性判定（安全底线）----
        # 视觉与 OCR 两路都不可用时，我们手里其实**没有任何内容证据**，
        # 此时给高分放行就是 fail-open —— 恶劣内容会被直接放过。
        # 按"可解释兜底"原则：证据不足一律转人工，且分数封顶。
        has_vision = bool(vision.get('available'))
        has_ocr = bool(ocr.get('available')) and bool(ocr.get('text'))
        has_kw = bool(kw.get('hits'))
        evidence_strength = sum([has_vision, has_ocr, has_kw])

        if evidence_strength == 0:
            # 零证据：绝不自动通过
            verdict = 'manual'
            score = min(score, 60)
            confidence = 0.2
            reasons.append(
                '⚠️ 视觉模型与 OCR 均不可用，当前没有任何内容证据；'
                '为避免漏判，本次不自动通过，已转人工复核'
            )
            suggestions = ['审核员人工确认后可通过', '安装 CLIP / EasyOCR 依赖可恢复自动审核']
        else:
            if hard_block:
                # 高危硬否决：即便扣分后仍在人工区间，也必须直接拒绝
                verdict = 'reject'
                score = min(score, 30)
                reasons.append('命中高危信号，按最小风险原则直接拒绝')
            elif score >= Config.AUDIT_PASS_THRESHOLD and not (set(categories) & high_risk):
                verdict = 'pass'
            elif score >= Config.AUDIT_MANUAL_THRESHOLD:
                verdict = 'manual'
            else:
                verdict = 'reject'
            confidence = 0.55 if has_vision else 0.4
            suggestions = ([] if verdict == 'pass' else
                           ['请删除相关片段后重新投稿', '如认为是误判，可在审核台提交申诉'])

        if not reasons:
            reasons.append('未发现任何违规信号：视觉、文字、元信息均正常')

        return {
            'verdict': verdict,
            'score': score,
            'confidence': confidence,
            'categories': categories,
            'reasons': reasons,
            'evidence': evidence,
            'suggestions': suggestions,
            'evidence_strength': evidence_strength,
        }


def rule_evidence_for(query: str) -> str:
    """供蓝图直接引用（避免循环导入）。"""
    return evidence_text(query)
