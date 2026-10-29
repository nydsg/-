"""人工审核台：待审队列 / 通过 / 拒绝 / 申诉处理。"""
import json
import logging
import os
import shutil

from flask import Blueprint, session

from ..config import Config
from ..db import use_cursor
from ..security import auditor_required, body, fail, ok
from ..ai import rag

logger = logging.getLogger(__name__)
bp = Blueprint('review', __name__)


@bp.get('/review/queue')
@auditor_required
def queue():
    """待审队列：优先展示 AI 已经跑过、结论为 manual 的（人机协作的落点）。"""
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT n.id, n.user_id, n.zuopin_url, n.tags, n.status, n.reason,
                   n.created_at, u.name AS uploader
            FROM NOAI_shenhe n LEFT JOIN users u ON n.user_id = u.id
            WHERE n.status = 'pending'
            ORDER BY n.created_at ASC LIMIT 30
        """)
        rows = cur.fetchall() or []

        cur.execute("""
            SELECT id, filename, verdict, score, confidence, created_at
            FROM audit_runs ORDER BY id DESC LIMIT 30
        """)
        runs = cur.fetchall() or []

    return ok(items=[{
        'id': r['id'], 'uploader': r.get('uploader') or '同学',
        'url': r.get('zuopin_url') or '', 'tags': r.get('tags') or '',
        'status': r['status'], 'reason': r.get('reason') or '',
        'created_at': str(r.get('created_at') or ''),
    } for r in rows], ai_runs=[{
        'id': x['id'], 'filename': x.get('filename'), 'verdict': x.get('verdict'),
        'score': x.get('score'), 'confidence': x.get('confidence'),
        'created_at': str(x.get('created_at') or ''),
    } for x in runs])


@bp.post('/review/<int:pending_id>/approve')
@auditor_required
def approve(pending_id: int):
    """过审：把暂存区文件挪进正式目录并发布。"""
    with use_cursor() as (_c, cur):
        cur.execute('SELECT * FROM NOAI_shenhe WHERE id = %s', (pending_id,))
        row = cur.fetchone()
    if not row:
        return fail('待审记录不存在', 404)

    url = row.get('zuopin_url') or ''
    filename = os.path.basename(url)
    stem, ext = os.path.splitext(filename)
    src = os.path.join(Config.NOAI_DIR, filename)
    folder = os.path.join(Config.UPLOAD_DIR, stem)
    os.makedirs(folder, exist_ok=True)
    dst = os.path.join(folder, filename)

    moved = False
    if os.path.isfile(src):
        try:
            shutil.move(src, dst)
            moved = True
        except Exception as e:
            logger.error('迁移文件失败: %s', e)

    uid = row['user_id']
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT username FROM users WHERE id = %s', (uid,))
        user = cur.fetchone() or {}
        cur.execute("""
            UPDATE video_records SET audit_result = 'visible',
                   zuopin_url = %s, zuopin_path = %s
            WHERE user_id = %s AND zuopin_url = %s
        """, (f'/uploads/{stem}/{filename}', dst, uid, url))
        if cur.rowcount == 0 and moved:
            cur.execute("""
                INSERT INTO video_records
                  (user_id, username, zuopin_name, saved_name, zuopin_path,
                   zuopin_url, file_size, upload_time, tags, audit_result)
                VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),%s,'visible')
            """, (uid, user.get('username'), filename, filename, dst,
                  f'/uploads/{stem}/{filename}', os.path.getsize(dst),
                  row.get('tags') or ''))
        cur.execute("DELETE FROM NOAI_shenhe WHERE id = %s", (pending_id,))

    # 判例回流：人工通过即一条“合规判例”，让 RAG 越用越准
    rag.save_case(title=f'人工通过：{filename}', content=f'标签：{row.get("tags") or "无"}',
                  labels=row.get('tags') or '人工通过', verdict='pass')
    return ok(message='已通过并发布')


@bp.post('/review/<int:pending_id>/reject')
@auditor_required
def reject(pending_id: int):
    data = body()
    reason = (data.get('reason') or '内容不符合社区规范').strip()[:200]
    with use_cursor(commit=True) as (_c, cur):
        cur.execute("""
            UPDATE NOAI_shenhe SET status = 'rejected', reason = %s WHERE id = %s
        """, (reason, pending_id))
        if cur.rowcount == 0:
            return fail('待审记录不存在', 404)
        cur.execute('SELECT zuopin_url FROM NOAI_shenhe WHERE id = %s', (pending_id,))
        row = cur.fetchone() or {}
    rag.save_case(title=f'人工拒绝：{os.path.basename(row.get("zuopin_url") or "")}',
                  content=reason, labels='人工拒绝', verdict='reject')
    return ok(message='已拒绝', reason=reason)


@bp.get('/review/appeals')
@auditor_required
def appeals():
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT id, zuopin_url, reason, created_at FROM NOAI_shenhe
            WHERE status = 'rejected' ORDER BY id DESC LIMIT 20
        """)
        rows = cur.fetchall() or []
    return ok(items=[{
        'id': r['id'], 'url': r.get('zuopin_url') or '',
        'reason': r.get('reason') or '', 'created_at': str(r.get('created_at') or ''),
    } for r in rows])


@bp.get('/review/cases')
@auditor_required
def cases():
    """判例库：查看 RAG 语料（技术文档里“越用越准”的证据）。"""
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT id, title, content, labels, verdict, source, created_at
            FROM audit_cases ORDER BY id DESC LIMIT 50
        """)
        rows = cur.fetchall() or []
    return ok(items=[{
        'id': r['id'], 'title': r.get('title'), 'content': r.get('content'),
        'labels': r.get('labels'), 'verdict': r.get('verdict'),
        'source': r.get('source'), 'created_at': str(r.get('created_at') or ''),
    } for r in rows], rules=len(rag.RULEBOOK))


@bp.post('/review/cases')
@auditor_required
def add_case():
    data = body()
    title = (data.get('title') or '').strip()
    if not title:
        return fail('判例标题不能为空')
    rag.save_case(title=title, content=(data.get('content') or '')[:2000],
                  labels=(data.get('labels') or '')[:255],
                  verdict=(data.get('verdict') or 'pass'))
    return ok(message='判例已入库，下次检索将命中')
