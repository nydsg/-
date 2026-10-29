"""★ AI 能力接口：流式创作助手 / 审核 Agent / RAG 检索 / 拟人化标签。

全部走 SSE（Server-Sent Events），前端逐字渲染 —— 对应赛题「流式输出渲染流畅」。
"""
from __future__ import annotations

import json
import logging
import os
import uuid

from flask import Blueprint, Response, request, session, stream_with_context

from ..config import Config
from ..db import use_cursor
from ..security import (body, ensure_inside, fail, login_required, ok,
                        safe_filename)
from ..ai.agent import AuditAgent
from ..ai.copilot import describe_and_write
from ..ai.llm import LLMClient, sse, stream_headers
from ..ai import rag, segment_audit

logger = logging.getLogger(__name__)
bp = Blueprint('ai', __name__)


def _stream(generator) -> Response:
    @stream_with_context
    def _gen():
        try:
            for ev in generator:
                yield sse(ev)
        except GeneratorExit:
            raise
        except Exception as e:                    # 流内异常也要能优雅收尾
            logger.exception('AI 流异常')
            yield sse({'type': 'error', 'message': str(e)})
            yield sse({'type': 'done'})
    return Response(_gen(), headers=stream_headers())


# ---------------- 能力探测 ----------------
@bp.get('/status')
def status():
    client = LLMClient()
    try:
        hits = rag.retrieve('校园内容规范 色情 暴力', 3)
    except Exception:
        hits = []
    return ok(
        ai_enabled=client.enabled,
        model=client.model,
        base_url=client.base_url if client.enabled else '',
        rag_docs=len(rag.RULEBOOK),
        rag_sample=hits,
        capabilities=['chat', 'audit-agent', 'segment-audit', 'copilot',
                      'rag', 'multimodal-frames'],
    )


# ---------------- 1. 创作助手（流式对话） ----------------
WRITER_PROMPT = """你是校园创作社区的「AI 创作搭子」，帮助学生把作品讲好。

风格：热情、具体、不说空话；每次给出可直接用的文案。
规则：
1. 需要标题时给 3 个候选，风格分别偏「真诚 / 有梗 / 文艺」；
2. 需要简介时控制在 80 字内，突出场景与情绪；
3. 需要标签时给 5 个，必须贴合校园场景；
4. 涉及违规风险的表达要主动提醒（但不代替审核结论）。"""


@bp.post('/chat')
def chat():
    data = body()
    message = (data.get('message') or '').strip()
    history = data.get('history') or []
    if not message:
        return fail('消息不能为空')
    if len(message) > 2000:
        return fail('单条消息最多 2000 字')

    messages = [{'role': 'system', 'content': WRITER_PROMPT}]
    for turn in history[-8:]:
        role = turn.get('role')
        content = (turn.get('content') or '')[:2000]
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    messages.append({'role': 'user', 'content': message})

    client = LLMClient()
    return _stream(_chat_events(client, messages))


def _chat_events(client: LLMClient, messages: list[dict]):
    yield {'type': 'start', 'model': client.model if client.enabled else 'demo'}
    for ev in client.stream(messages):
        if ev['type'] == 'delta':
            yield {'type': 'delta', 'text': ev['text']}
        elif ev['type'] == 'error':
            yield ev
            yield {'type': 'done'}
            return
        elif ev['type'] == 'done':
            yield ev


# ---------------- 2. 审核 Agent（SSE 时间线） ----------------
def _resolve_video(payload: dict):
    """解析视频来源，返回 (video_path, filename, error_response)。

    四种输入：待审记录的 pending_id、已上传的 video_id、现场上传的 file、
    已在目录里的 filename。审核、切片审核、创作副驾三条流共用这段逻辑。
    """
    pending_id = request.form.get('pending_id') or payload.get('pending_id')
    video_id = request.form.get('video_id') or payload.get('video_id')
    filename = request.form.get('filename') or payload.get('filename') or ''
    video_path = ''

    if pending_id:
        # 审核台、AI 工坊常用：直接给待审记录 id，后端把 zuopin_url 映射回磁盘路径
        try:
            pid = int(pending_id)
        except (TypeError, ValueError):
            return '', '', fail('pending_id 非法')
        with use_cursor() as (_c, cur):
            cur.execute('SELECT zuopin_url FROM NOAI_shenhe WHERE id = %s', (pid,))
            row = cur.fetchone()
        if not row:
            return '', '', fail('待审记录不存在', 404)
        url = row.get('zuopin_url') or ''
        rel = url.split('/uploads/', 1)[1] if '/uploads/' in url else ''
        if not rel:
            return '', '', fail('该待审记录没有可用的视频文件', 404)
        cand = os.path.join(Config.UPLOAD_DIR, *[p for p in rel.split('/') if p])
        if not os.path.isfile(cand) or not ensure_inside(Config.UPLOAD_DIR, cand):
            return '', '', fail('待审视频文件已丢失', 404)
        return cand, os.path.basename(cand), None

    if video_id:
        try:
            vid = int(video_id)
        except (TypeError, ValueError):
            return '', '', fail('video_id 非法')
        with use_cursor() as (_c, cur):
            cur.execute('SELECT zuopin_path, zuopin_name FROM video_records WHERE id = %s',
                        (vid,))
            row = cur.fetchone()
        if not row:
            return '', '', fail('作品不存在', 404)
        video_path = row.get('zuopin_path') or ''
        filename = filename or row.get('zuopin_name') or ''
        return video_path, filename, None

    if 'file' in request.files:
        file = request.files['file']
        stem, ext = os.path.splitext(os.path.basename(file.filename or 'upload.mp4'))
        if ext.lower() not in Config.ALLOWED_VIDEO_EXT:
            return '', '', fail('仅支持 mp4 / mov')
        os.makedirs(Config.TEMP_DIR, exist_ok=True)
        video_path = os.path.join(
            Config.TEMP_DIR, f'ai_{uuid.uuid4().hex[:8]}_{safe_filename(stem)}{ext}')
        if not ensure_inside(Config.TEMP_DIR, video_path):
            return '', '', fail('非法文件名')
        file.save(video_path)
        return video_path, filename or file.filename, None

    if filename:
        # 演示/联调便利：允许直接传已在临时目录或上传目录里的文件名
        for root in (Config.TEMP_DIR, Config.UPLOAD_DIR):
            cand = os.path.join(root, os.path.basename(filename))
            if os.path.isfile(cand) and ensure_inside(root, cand):
                return cand, filename, None
        return '', '', fail('找不到该视频文件，请改用 video_id 或上传文件', 404)

    return '', '', fail('请提供 video_id、file 或 filename')


@bp.post('/audit/stream')
@login_required
def audit_stream():
    """两种输入：已上传的 video_id，或现场上传的临时文件。"""
    payload = body() if request.is_json else {}
    video_path, filename, err = _resolve_video(payload)
    if err:
        return err
    video_id = request.form.get('video_id') or payload.get('video_id')
    agent = AuditAgent()
    return _stream(_audit_events(agent, video_path, filename, video_id))


# ---------------- 2.1 创作副驾：看视频写文案（SSE） ----------------
@bp.post('/copilot/stream')
@login_required
def copilot_stream():
    """上传一个视频，让 AI 看完后直接给出标题/简介/标签。

    解决"用户说不清自己拍了什么"的场景：不需要文字描述，直接看画面。
    """
    payload = body() if request.is_json else {}
    video_path, filename, err = _resolve_video(payload)
    if err:
        return err
    hint = (request.form.get('hint') or payload.get('hint') or '').strip()[:300]
    return _stream(describe_and_write(video_path, filename, hint))


@bp.post('/audit/segment/stream')
@login_required
def audit_segment_stream():
    """切片审核：按时间切成若干段，逐段交给多模态模型，返回可定位到秒的结论。

    与 /audit/stream 的区别：
        /audit/stream          → 整片一个结论（能不能发）
        /audit/segment/stream  → 逐段结论 + 时间轴（哪几秒有问题）
    两者并存，互不影响。
    """
    payload = body() if request.is_json else {}
    video_path, filename, err = _resolve_video(payload)
    if err:
        return err

    def _num(key, default=None):
        raw = request.form.get(key) or payload.get(key)
        if raw in (None, ''):
            return default
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    segment_seconds = _num('segment_seconds')
    max_segments = _num('max_segments')
    video_id = request.form.get('video_id') or payload.get('video_id')

    def _events():
        yield {'type': 'start', 'filename': filename, 'mode': 'segment',
               'ai_enabled': bool(Config.AI_API_KEY)}
        result_holder = {}
        for ev in segment_audit.run(
                video_path, filename,
                segment_seconds=segment_seconds,
                max_segments=max_segments):
            if ev.get('type') == 'result':
                result_holder = ev
            yield ev
        _persist_audit(result_holder, filename, video_id)

    return _stream(_events())


def _persist_audit(result_holder: dict, filename: str, video_id=None) -> None:
    """把审核结论落库：audit_runs 存证据链，video_records 更新状态。

    整片审核与切片审核共用（切片审核把时间轴放进 evidence 字段）。
    """
    if not result_holder:
        return
    try:
        verdict = result_holder.get('verdict', 'manual')
        evidence = result_holder.get('evidence')
        if evidence is None and result_holder.get('segments'):
            # 切片模式：用段级时间轴当证据链
            evidence = []
            for s in result_holder['segments'][:10]:
                detail = str(s.get('verdict') or '')
                if s.get('scene'):
                    detail += f"｜{s['scene']}"
                if s.get('risks'):
                    detail += '｜' + '、'.join(
                        str(r.get('type') or '') for r in s['risks'])
                evidence.append({'type': 'segment',
                                 'ref': f"{s.get('start')}–{s.get('end')}s",
                                 'detail': detail})
        with use_cursor(commit=True) as (_c, cur):
            cur.execute("""
                INSERT INTO audit_runs
                  (video_id, user_id, filename, verdict, score, confidence,
                   evidence, model, latency_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (video_id, session.get('user_id'), (filename or '')[:255], verdict,
                  result_holder.get('score'), result_holder.get('confidence'),
                  json.dumps(evidence or [], ensure_ascii=False),
                  result_holder.get('model'), result_holder.get('elapsed_ms')))
            if video_id:
                status = {'pass': 'visible', 'reject': 'rejected'}.get(verdict, 'pending')
                cur.execute('UPDATE video_records SET audit_result = %s WHERE id = %s',
                            (status, int(video_id)))
    except Exception as e:
        logger.warning('审核结果落库失败: %s', e)


def _audit_events(agent: AuditAgent, video_path: str, filename: str, video_id):
    yield {'type': 'start', 'filename': filename,
           'ai_enabled': agent.llm.enabled}
    result_holder = {}
    for ev in agent.run(video_path=video_path, filename=filename):
        if ev.get('type') == 'result':
            result_holder = ev
        yield ev
    _persist_audit(result_holder, filename, video_id)


# ---------------- 3. RAG 检索（可单独演示） ----------------
@bp.get('/rag/search')
def rag_search():
    query = (request.args.get('q') or '').strip()
    top_k = min(max(int(request.args.get('k') or 4), 1), 10)
    if not query:
        query = '校园内容规范'
    hits = rag.retrieve(query, top_k)
    return ok(query=query, items=hits, total=len(hits),
              corpus_size=len(rag.RULEBOOK))


# ---------------- 4. 生成标签（结构化输出演示） ----------------
@bp.post('/tags')
def gen_tags():
    data = body()
    title = (data.get('title') or '').strip()
    desc = (data.get('desc') or '').strip()
    if not title:
        return fail('请先填写作品标题')

    client = LLMClient()
    if not client.enabled:
        # 无 key 时有确定性的兜底，保证演示不断
        base = [t for t in ('校园日常', '青春记录', '我的大学生活') if t]
        return ok(tags=base, source='fallback', ai_enabled=False)

    prompt = (f"作品标题：{title}\n内容简介：{desc or '（无）'}\n\n"
              "请输出 5 个校园场景标签，严格 JSON：{\"tags\":[\"...\"]}，"
              "不要解释、不要代码块。")
    try:
        res = client.chat([
            {'role': 'system', 'content': '你是校园内容标签专家，只输出 JSON。'},
            {'role': 'user', 'content': prompt},
        ])
        text = (res.content or '').strip().strip('`')
        start, end = text.find('{'), text.rfind('}')
        tags = json.loads(text[start:end + 1]).get('tags', []) if start >= 0 else []
        return ok(tags=tags[:5], source='llm', ai_enabled=True, latency_ms=res.latency_ms)
    except Exception as e:
        logger.warning('标签生成失败: %s', e)
        return ok(tags=['校园日常'], source='fallback', ai_enabled=True, error=str(e))


# ---------------- 5. 审核历史 ----------------
@bp.get('/audit/history')
@login_required
def audit_history():
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT id, filename, verdict, score, confidence, model, latency_ms, created_at
            FROM audit_runs ORDER BY id DESC LIMIT 20
        """)
        rows = cur.fetchall() or []
    return ok(items=[{
        'id': r['id'], 'filename': r.get('filename'), 'verdict': r.get('verdict'),
        'score': r.get('score'), 'confidence': r.get('confidence'),
        'model': r.get('model'), 'latency_ms': r.get('latency_ms'),
        'created_at': str(r.get('created_at') or ''),
    } for r in rows])
