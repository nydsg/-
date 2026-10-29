"""切片审核 Agent：把视频切成若干段，逐段交给多模态模型判定，最后聚合出总结论。

与整片审核（app/ai/agent.py）的分工：
    整片审核回答"这条内容能不能发"（一个结论 + 证据链）；
    切片审核回答"**哪一段、第几秒**出了问题"（逐段时间轴 + 定位）。

聚合策略（从严）：
    任一段 high（如裸露/血腥/赌博/代考引流）→ 整体 reject，分数据该段封顶；
    任一段 medium                          → 整体 manual；
    全部 low / 无风险                      → 整体 pass；
    所有段都没拿到证据                     → 整体 manual（fail-closed，绝不自动放行）。

事件协议（SSE）：
    {'type':'plan',    'plan':{duration,count,segments:[...]}}
    {'type':'segment', 'segment':{index,start,end,verdict,score,risks,scene,tags}}
    {'type':'result',  ...聚合结论 + segments 时间轴 + 可选的 LLM 说明}
    {'type':'done'}
"""
from __future__ import annotations

import logging
import time

from ..config import Config
from . import tools
from .llm import LLMClient
from .segmenter import cleanup_segment_dir, extract_segment_frames, plan_segments

logger = logging.getLogger(__name__)

# 段级判定参数
_SEV_WEIGHT = {'high': 0, 'medium': 1, 'low': 2}


def _segment_verdict(vlm: dict) -> dict:
    """单段判定：把该段的风险列表压成一个 verdict / score。"""
    if not vlm.get('ok'):
        return {'verdict': 'unknown', 'score': 50, 'confidence': 0.2,
                'risks': [], 'note': vlm.get('note') or '多模态不可用',
                'categories': []}

    risks = vlm.get('risks') or []
    worst = None
    for r in risks:
        sev = str(r.get('severity') or 'low').lower()
        if worst is None or _SEV_WEIGHT.get(sev, 3) < _SEV_WEIGHT.get(worst, 3):
            worst = sev

    if worst == 'high':
        return {'verdict': 'reject', 'score': 15, 'confidence': 0.85,
                'risks': risks, 'categories': vlm.get('categories') or [],
                'note': '该段出现高危内容'}
    if worst == 'medium':
        return {'verdict': 'manual', 'score': 55, 'confidence': 0.55,
                'risks': risks, 'categories': vlm.get('categories') or [],
                'note': '该段存在需人工确认的风险'}
    if worst == 'low':
        return {'verdict': 'manual', 'score': 72, 'confidence': 0.45,
                'risks': risks, 'categories': vlm.get('categories') or [],
                'note': '该段有轻微风险提示'}
    return {'verdict': 'pass', 'score': 95, 'confidence': 0.6,
            'risks': [], 'categories': [], 'note': '该段未见风险'}


def _aggregate(segments: list[dict]) -> dict:
    """从段级结论聚合出整体结论（从严 + fail-closed）。"""
    if not segments:
        return {'verdict': 'manual', 'score': 50, 'confidence': 0.2,
                'categories': [], 'reasons': ['没有可用的切片结果，转人工'],
                'evidence_strength': 0}

    verdicts = [s.get('verdict') for s in segments]
    scored = [s for s in segments if s.get('verdict') != 'unknown']
    categories: list[str] = []
    reasons: list[str] = []
    for s in segments:
        for c in s.get('categories') or []:
            if c not in categories:
                categories.append(c)

    if 'reject' in verdicts:
        overall = 'reject'
        score = min(s.get('score', 50) for s in segments)
        bad = [s for s in segments if s.get('verdict') == 'reject']
        for s in bad[:3]:
            detail = '；'.join(f"{r.get('type')}({r.get('severity')})"
                               for r in (s.get('risks') or [])[:2])
            reasons.append(f"第 {s['start']}–{s['end']} 秒判定为高危：{detail}")
        confidence = 0.85
    elif 'manual' in verdicts:
        overall = 'manual'
        score = min(s.get('score', 60) for s in segments)
        bad = [s for s in segments if s.get('verdict') == 'manual']
        for s in bad[:3]:
            detail = '；'.join(f"{r.get('type')}({r.get('severity')})"
                               for r in (s.get('risks') or [])[:2]) or s.get('note', '')
            reasons.append(f"第 {s['start']}–{s['end']} 秒需要人工确认：{detail}")
        confidence = 0.55
    elif 'unknown' in verdicts:
        overall = 'manual'
        score = 50
        reasons.append('部分切片未能完成多模态识别，为避免漏判，整体转人工')
        confidence = 0.25
    else:
        overall = 'pass'
        score = 95
        reasons.append(f'{len(segments)} 个切片均未发现风险')
        confidence = 0.6

    # fail-closed 总闸：完全没有证据时绝不自动通过
    evidence_strength = len(scored)
    if evidence_strength == 0 and overall == 'pass':
        overall = 'manual'
        score = min(score, 60)
        confidence = 0.2
        reasons.insert(0, '⚠️ 所有切片均无有效证据，已转人工复核')

    return {'verdict': overall, 'score': int(score), 'confidence': confidence,
            'categories': categories, 'reasons': reasons,
            'evidence_strength': evidence_strength}


def run(video_path: str, filename: str = '',
        segment_seconds: int | None = None,
        max_segments: int | None = None,
        with_llm: bool = True):
    """生成器：规划切片 → 逐段审核 → 聚合。逐事件 yield 供 SSE 推送。"""
    started = time.time()

    plan = plan_segments(video_path, segment_seconds, max_segments)
    if not plan.get('ok'):
        yield {'type': 'error',
               'message': plan.get('error') or '切片规划失败，无法读取视频'}
        yield {'type': 'done'}
        return

    yield {'type': 'plan',
           'plan': {'duration': plan['duration'], 'count': plan['count'],
                    'segment_seconds': plan.get('segment_seconds'),
                    'segments': plan['segments'],
                    'meta': plan.get('meta') or {}}}

    extracted = extract_segment_frames(video_path, plan)
    if not extracted.get('ok'):
        yield {'type': 'error',
               'message': extracted.get('error') or '切片抽帧失败'}
        yield {'type': 'done'}
        return

    seg_dir = extracted.get('dir') or ''
    results: list[dict] = []
    try:
        for seg in extracted['segments']:
            frame_paths = [f['path'] for f in seg.get('frames') or []]
            t0 = time.time()
            if frame_paths:
                vlm = tools.call_tool('vlm_describe', {
                    'frame_paths': frame_paths, 'filename': filename,
                    'max_frames': 1})
            else:
                vlm = {'ok': False, 'note': '该切片未抽到可用帧'}
            judged = _segment_verdict(vlm)

            scenes = [str(f.get('scene') or '') for f in (vlm.get('frames') or [])]
            scenes = [s for s in dict.fromkeys(scenes) if s]
            # 画面文字继续走关键词交叉验证
            text = (vlm.get('text') or '').strip()
            kw_hits = {}
            if text:
                kw_hits = (tools.keyword_risk(text).get('hits') or {})

            seg_result = {
                'index': seg['index'],
                'start': seg['start'],
                'end': seg['end'],
                'verdict': judged['verdict'],
                'score': judged['score'],
                'confidence': judged['confidence'],
                'categories': judged['categories'],
                'risks': judged['risks'][:3],
                'scene': scenes[0] if scenes else '',
                'tags': (vlm.get('tags') or [])[:5],
                'text': text[:200],
                'kw_hits': kw_hits,
                'note': judged['note'],
                'frame_ts': (seg.get('frames') or [{}])[0].get('ts'),
                'motion': (seg.get('frames') or [{}])[0].get('motion'),
                'latency_ms': int((time.time() - t0) * 1000),
            }
            results.append(seg_result)
            yield {'type': 'segment', 'segment': seg_result}
    finally:
        cleanup_segment_dir(seg_dir)

    overall = _aggregate(results)

    # 可选：让文本模型把逐段发现写成一段人话
    narrative = ''
    if with_llm and LLMClient().enabled:
        try:
            lines = [
                f"第{s['start']}–{s['end']}秒：{s['verdict']}，"
                f"场景「{s['scene'] or '未知'}」"
                + (f"，风险 {'、'.join(r.get('type','') for r in s['risks'])}"
                   if s['risks'] else '')
                for s in results
            ]
            prompt = (
                '你是一名校园内容审核员。下面是把一条视频切片审核后的逐段结论，'
                '请用 2~3 句中文总结：整体是否可发、问题集中在哪几秒、给作者什么建议。'
                '不要罗列数据，直接说结论。\n\n' + '\n'.join(lines) +
                f"\n\n整体判定：{overall['verdict']}（{overall['score']} 分）"
            )
            res = LLMClient().chat([
                {'role': 'system', 'content': '你是简洁、专业的内容审核助手。'},
                {'role': 'user', 'content': prompt}],
                temperature=0.3)
            narrative = (res.content or '').strip()[:600]
        except Exception as e:
            logger.warning('切片审核总结生成失败: %s', e)

    yield {
        'type': 'result',
        'mode': 'segment',
        **overall,
        'narrative': narrative,
        'segments': results,
        'segment_seconds': plan.get('segment_seconds'),
        'duration': plan.get('duration'),
        'filename': filename,
        'elapsed_ms': int((time.time() - started) * 1000),
        'ai_enabled': bool(Config.AI_API_KEY),
        'model': Config.AI_VISION_MODEL or Config.AI_MODEL,
    }
    yield {'type': 'done'}
