"""作品：列表 / 详情 / 投稿 / 播放量 / 我的作品 / 删除。"""
import logging
import os
import time
import uuid

from flask import Blueprint, request, session

from ..config import Config
from ..db import use_cursor
from ..security import (as_int, body, ensure_inside, fail, is_authenticated,
                        login_required, ok, paging, safe_filename)

logger = logging.getLogger(__name__)
bp = Blueprint('videos', __name__)

VIDEO_FIELDS = ('v.id, v.user_id, v.zuopin_name, v.zuopin_desc, v.zuopin_url, v.cover_path, '
                'v.file_size, v.upload_time, v.view_count, v.tags, v.username, '
                'u.name AS author_name, u.touxiang_url AS author_avatar')


def _row_to_card(row: dict) -> dict:
    return {
        'id': row['id'],
        'title': row.get('zuopin_name') or '未命名作品',
        'desc': row.get('zuopin_desc') or '',
        'url': row.get('zuopin_url') or '',
        'cover': row.get('cover_path') or '/static/jpg/home.jpg',
        'views': row.get('view_count') or 0,
        'tags': [t for t in (row.get('tags') or '').split(',') if t],
        'author': row.get('author_name') or row.get('username') or '同学',
        'author_id': row.get('user_id'),
        'author_avatar': row.get('author_avatar') or '',
        'created_at': str(row.get('upload_time') or ''),
        'duration': row.get('duration') or '',
    }


@bp.get('/videos')
def list_videos():
    """支持分页、标签筛选、关键词筛选。"""
    page, size, offset = paging()
    tag = (request.args.get('tag') or '').strip()
    keyword = (request.args.get('q') or '').strip()
    sort = request.args.get('sort', 'new')      # new | hot

    where, params = [], []
    if tag:
        where.append('v.tags LIKE %s')
        params.append(f'%{tag}%')
    if keyword:
        # 简介也纳入搜索：作者写的文字描述往往是最好用的检索素材
        where.append('(v.zuopin_name LIKE %s OR v.tags LIKE %s OR v.zuopin_desc LIKE %s)')
        params += [f'%{keyword}%', f'%{keyword}%', f'%{keyword}%']
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''
    order = 'v.view_count DESC' if sort == 'hot' else 'v.upload_time DESC'

    with use_cursor() as (_c, cur):
        cur.execute(f'SELECT COUNT(*) AS c FROM video_records v {where_sql}', params)
        total = (cur.fetchone() or {}).get('c', 0)
        cur.execute(
            f'SELECT {VIDEO_FIELDS} FROM video_records v '
            f'LEFT JOIN users u ON v.user_id = u.id {where_sql} '
            f'ORDER BY {order} LIMIT %s OFFSET %s',
            params + [size, offset])
        rows = cur.fetchall() or []

    return ok(items=[_row_to_card(r) for r in rows], total=total,
              page=page, size=size, has_more=page * size < total)


@bp.get('/videos/<int:video_id>')
def detail(video_id: int):
    with use_cursor() as (_c, cur):
        cur.execute(
            f'SELECT {VIDEO_FIELDS}, v.audit_result, v.violations '
            f'FROM video_records v LEFT JOIN users u ON v.user_id = u.id '
            f'WHERE v.id = %s', (video_id,))
        row = cur.fetchone()
        if not row:
            return fail('作品不存在', 404)
        cur.execute('SELECT COUNT(*) AS c FROM dianzan WHERE video_id = %s', (video_id,))
        likes = (cur.fetchone() or {}).get('c', 0)
        cur.execute('SELECT COUNT(*) AS c FROM shoucang WHERE video_id = %s', (video_id,))
        collects = (cur.fetchone() or {}).get('c', 0)
        cur.execute('SELECT COUNT(*) AS c FROM comments WHERE video_id = %s', (video_id,))
        comments = (cur.fetchone() or {}).get('c', 0)

    card = _row_to_card(row)
    card.update(likes=likes, collects=collects, comment_count=comments,
                audit_result=row.get('audit_result'), violations=row.get('violations'))
    return ok(video=card)


@bp.post('/videos/<int:video_id>/view')
def incr_view(video_id: int):
    """播放量 +1（同一会话 30 分钟内只计一次，避免刷量）。"""
    seen = session.get('viewed') or []
    now = time.time()
    key = str(video_id)
    fresh = [k for k in seen if now - float(k.split(':')[1]) < 1800] \
        if seen and ':' in str(seen[0]) else []
    if any(k.split(':')[0] == key for k in fresh):
        return ok(counted=False)
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('UPDATE video_records SET view_count = view_count + 1 WHERE id = %s',
                    (video_id,))
        cur.execute('SELECT view_count FROM video_records WHERE id = %s', (video_id,))
        views = (cur.fetchone() or {}).get('view_count', 0)
    fresh = [k for k in fresh if k.split(':')[0] != key] + [f'{key}:{now}']
    session['viewed'] = fresh[-50:]
    return ok(counted=True, views=views)


@bp.get('/videos/mine')
@login_required
def my_videos():
    uid = session['user_id']
    with use_cursor() as (_c, cur):
        cur.execute(
            f'SELECT {VIDEO_FIELDS}, v.audit_result FROM video_records v '
            f'LEFT JOIN users u ON v.user_id = u.id '
            f'WHERE v.user_id = %s ORDER BY v.upload_time DESC', (uid,))
        rows = cur.fetchall() or []
    return ok(items=[{**_row_to_card(r), 'status': r.get('audit_result') or 'visible'}
                     for r in rows])


@bp.delete('/videos/<int:video_id>')
@login_required
def delete_video(video_id: int):
    uid = session['user_id']
    with use_cursor() as (_c, cur):
        cur.execute('SELECT * FROM video_records WHERE id = %s AND user_id = %s',
                    (video_id, uid))
        row = cur.fetchone()
    if not row:
        return fail('作品不存在或无权限', 403)

    with use_cursor(commit=True) as (_c, cur):
        for table in ('notifications', 'comments', 'dianzan', 'shoucang', 'lishi'):
            cur.execute(f'DELETE FROM {table} WHERE video_id = %s', (video_id,))
        cur.execute('DELETE FROM video_records WHERE id = %s', (video_id,))

    # 只删作品自己的目录，且必须在 uploads 内（防误删整个目录）
    path = row.get('zuopin_path')
    if path:
        target = os.path.dirname(path)
        if ensure_inside(Config.UPLOAD_DIR, target) and \
                os.path.abspath(target) != os.path.abspath(Config.UPLOAD_DIR):
            import shutil
            shutil.rmtree(target, ignore_errors=True)
    return ok(message='已删除')


@bp.post('/videos/<int:video_id>/status')
@login_required
def set_status(video_id: int):
    """下架 / 恢复。"""
    status = (body().get('status') or 'hidden').strip()
    if status not in ('visible', 'hidden'):
        return fail('状态只能是 visible 或 hidden')
    uid = session['user_id']
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('UPDATE video_records SET audit_result = %s '
                    'WHERE id = %s AND user_id = %s', (status, video_id, uid))
        if cur.rowcount == 0:
            return fail('作品不存在或无权限', 403)
    return ok(status=status)


# ---------------- 投稿（人工审核通道 + AI 预审） ----------------
@bp.post('/videos/upload')
@login_required
def upload():
    if 'file' not in request.files:
        return fail('请选择视频文件')
    file = request.files['file']
    raw_name = os.path.basename((file.filename or '').replace('\\', '/'))
    stem, ext = os.path.splitext(raw_name)
    ext = ext.lower()
    if ext not in Config.ALLOWED_VIDEO_EXT:
        return fail(f'不支持的格式 {ext}，仅支持 mp4 / mov')

    clean = safe_filename(stem)
    folder_name = f'{clean}_{uuid.uuid4().hex[:6]}{ext}'
    folder = os.path.join(Config.UPLOAD_DIR, folder_name)
    if not ensure_inside(Config.UPLOAD_DIR, folder):
        return fail('非法文件名')
    os.makedirs(folder, exist_ok=True)
    stored = os.path.join(folder, f'{clean}{ext}')
    file.save(stored)

    size = os.path.getsize(stored)
    tags = (request.form.get('tags') or '').strip()[:255]
    title = (request.form.get('title') or raw_name)[:200]
    # 作品简介（可选）：AI 创作副驾生成的文案可以直接带过来
    desc = (request.form.get('desc') or request.form.get('intro') or '').strip()[:1000]
    url = f'/uploads/{folder_name}/{clean}{ext}'

    # 封面（有 opencv 才做，失败不影响投稿）
    cover = None
    try:
        from ..ai.tools import extract_frames
        res = extract_frames(stored, max_frames=1)
        frames = res.get('frames') or []
        if frames:
            cover_url = f'/uploads/{folder_name}/cover.jpg'
            cover_abs = os.path.join(folder, 'cover.jpg')
            try:
                import shutil as _sh
                _sh.copyfile(frames[0]['path'], cover_abs)
                cover = cover_url
            except Exception:
                cover = None
    except Exception as e:
        logger.debug('封面生成跳过: %s', e)

    uid = session['user_id']
    with use_cursor(commit=True) as (_c, cur):
        cur.execute("""
            INSERT INTO NOAI_shenhe (user_id, zuopin_url, tags, status, reason)
            VALUES (%s, %s, %s, 'pending', '等待审核')
        """, (uid, url, tags))
        pending_id = cur.lastrowid
        cur.execute("""
            INSERT INTO video_records
              (user_id, username, zuopin_name, zuopin_desc, saved_name, zuopin_path,
               zuopin_url, cover_path, file_size, upload_time, tags, audit_result)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,'pending')
        """, (uid, session.get('username'), title, desc or None, f'{clean}{ext}', stored,
              url, cover, size, tags))
        video_id = cur.lastrowid

    return ok(video_id=video_id, pending_id=pending_id, url=url, cover=cover,
              file_size=size, message='投稿成功，已进入审核队列')
