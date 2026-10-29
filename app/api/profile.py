"""个人主页与设置：资料、头像、背景、签名、搜索。"""
import logging
import os
import uuid

from flask import Blueprint, request

from ..config import Config
from ..db import use_cursor
from ..security import (body, ensure_inside, fail, is_authenticated,
                        login_required, ok, paging, safe_filename)
from flask import session

logger = logging.getLogger(__name__)
bp = Blueprint('profile', __name__)


@bp.get('/users/<int:user_id>')
def public_profile(user_id: int):
    """公开主页：资料 + 作品 + 统计。"""
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT id, username, name, school, signature, touxiang_url, bg_url, created_at
            FROM users WHERE id = %s
        """, (user_id,))
        user = cur.fetchone()
        if not user:
            return fail('用户不存在', 404)

        cur.execute("""
            SELECT v.id, v.zuopin_name, v.cover_path, v.zuopin_url, v.view_count,
                   v.tags, v.upload_time
            FROM video_records v
            WHERE v.user_id = %s AND (v.audit_result IS NULL OR v.audit_result <> 'hidden')
            ORDER BY v.upload_time DESC LIMIT 30
        """, (user_id,))
        works = cur.fetchall() or []

        cur.execute('SELECT COUNT(*) AS c FROM follows WHERE followed_id = %s', (user_id,))
        fans = (cur.fetchone() or {}).get('c', 0)
        cur.execute('SELECT COUNT(*) AS c FROM follows WHERE follower_id = %s', (user_id,))
        following = (cur.fetchone() or {}).get('c', 0)
        cur.execute("""
            SELECT COUNT(*) AS c FROM dianzan d
            JOIN video_records v ON d.video_id = v.id WHERE v.user_id = %s
        """, (user_id,))
        likes = (cur.fetchone() or {}).get('c', 0)

    return ok(user={
        'id': user['id'],
        'username': user['username'],
        'name': user.get('name') or user['username'],
        'school': user.get('school') or '未公开',
        'signature': user.get('signature') or '',
        'avatar': user.get('touxiang_url') or '',
        'bg': user.get('bg_url') or '',
        'joined_at': str(user.get('created_at') or ''),
    }, works=[{
        'id': w['id'], 'title': w.get('zuopin_name') or '未命名',
        'cover': w.get('cover_path') or '/static/jpg/home.jpg',
        'url': w.get('zuopin_url') or '', 'views': w.get('view_count') or 0,
        'tags': [t for t in (w.get('tags') or '').split(',') if t],
    } for w in works], stats={'fans': fans, 'following': following, 'likes': likes})


@bp.patch('/users/me')
@login_required
def update_me():
    data = body()
    uid = session['user_id']
    fields, params = [], []
    for col, key in (('name', 'name'), ('school', 'school'),
                     ('signature', 'signature'), ('email', 'email')):
        if key in data and data[key] is not None:
            value = str(data[key]).strip()[:200]
            if key == 'name' and not value:
                return fail('昵称不能为空')
            fields.append(f'{col} = %s')
            params.append(value)
    if not fields:
        return fail('没有需要更新的字段')
    params.append(uid)
    with use_cursor(commit=True) as (_c, cur):
        cur.execute(f'UPDATE users SET {", ".join(fields)} WHERE id = %s', params)
    return ok(message='已保存')


def _save_image(field_name: str, subdir: str, allowed: set[str]) -> str:
    file = request.files.get(field_name)
    if not file or not file.filename:
        raise ValueError('未选择文件')
    ext = os.path.splitext(os.path.basename(file.filename))[1].lower()
    if ext not in allowed:
        raise ValueError(f'只支持 {"/".join(sorted(allowed))}')
    name = f'{uuid.uuid4().hex}{ext}'
    target_dir = subdir
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, name)
    if not ensure_inside(target_dir, path):
        raise ValueError('非法文件名')
    file.save(path)
    return name


@bp.post('/users/me/avatar')
@login_required
def upload_avatar():
    try:
        name = _save_image('avatar', Config.AVATAR_DIR, Config.ALLOWED_IMAGE_EXT)
    except ValueError as e:
        return fail(str(e))
    url = f'/static/avatars/{name}'
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('UPDATE users SET touxiang_url = %s WHERE id = %s',
                    (url, session['user_id']))
    return ok(avatar=url)


@bp.post('/users/me/background')
@login_required
def upload_background():
    try:
        name = _save_image('bg', Config.BG_DIR, Config.ALLOWED_IMAGE_EXT)
    except ValueError as e:
        return fail(str(e))
    url = f'/static/backgrounds/{name}'
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('UPDATE users SET bg_url = %s WHERE id = %s',
                    (url, session['user_id']))
    return ok(bg=url)


@bp.get('/search')
def search():
    """搜索：作品 + 用户。关键词走高亮，分页返回。"""
    keyword = (request.args.get('q') or '').strip()
    page, size, offset = paging(12, 40)
    if not keyword:
        return ok(items=[], total=0, users=[], keyword='')

    like = f'%{keyword}%'
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT v.id, v.zuopin_name, v.cover_path, v.zuopin_url, v.view_count,
                   v.tags, u.name AS author_name
            FROM video_records v LEFT JOIN users u ON v.user_id = u.id
            WHERE (v.zuopin_name LIKE %s OR v.tags LIKE %s)
              AND (v.audit_result IS NULL OR v.audit_result <> 'hidden')
            ORDER BY v.view_count DESC LIMIT %s OFFSET %s
        """, (like, like, size, offset))
        rows = cur.fetchall() or []
        cur.execute("""
            SELECT COUNT(*) AS c FROM video_records v
            WHERE (v.zuopin_name LIKE %s OR v.tags LIKE %s)
              AND (v.audit_result IS NULL OR v.audit_result <> 'hidden')
        """, (like, like))
        total = (cur.fetchone() or {}).get('c', 0)
        cur.execute("""
            SELECT id, name, username, school, touxiang_url
            FROM users WHERE name LIKE %s OR username LIKE %s LIMIT 5
        """, (like, like))
        users = cur.fetchall() or []

    return ok(keyword=keyword, total=total, page=page, size=size,
              items=[{
                  'id': r['id'], 'title': r.get('zuopin_name') or '未命名',
                  'cover': r.get('cover_path') or '/static/jpg/home.jpg',
                  'url': r.get('zuopin_url') or '',
                  'views': r.get('view_count') or 0,
                  'author': r.get('author_name') or '同学',
                  'tags': [t for t in (r.get('tags') or '').split(',') if t],
              } for r in rows],
              users=[{
                  'id': u['id'], 'name': u.get('name') or u.get('username'),
                  'school': u.get('school') or '', 'avatar': u.get('touxiang_url') or '',
              } for u in users])


@bp.get('/shorts')
def shorts():
    """首页推荐流：近期热门 + 标签聚合（无 AI 也能用的确定性推荐）。"""
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT v.id, v.zuopin_name, v.cover_path, v.zuopin_url, v.view_count,
                   v.tags, u.name AS author_name
            FROM video_records v LEFT JOIN users u ON v.user_id = u.id
            WHERE v.audit_result IS NULL OR v.audit_result <> 'hidden'
            ORDER BY v.view_count DESC, v.upload_time DESC LIMIT 12
        """)
        rows = cur.fetchall() or []
        cur.execute("""
            SELECT tags FROM video_records
            WHERE tags IS NOT NULL AND tags <> '' LIMIT 200
        """)
        raw_tags = cur.fetchall() or []

    counter: dict[str, int] = {}
    for r in raw_tags:
        for t in (r.get('tags') or '').split(','):
            t = t.strip()
            if t:
                counter[t] = counter.get(t, 0) + 1
    top_tags = sorted(counter.items(), key=lambda kv: -kv[1])[:12]

    return ok(items=[{
        'id': r['id'], 'title': r.get('zuopin_name') or '未命名',
        'cover': r.get('cover_path') or '/static/jpg/home.jpg',
        'url': r.get('zuopin_url') or '', 'views': r.get('view_count') or 0,
        'author': r.get('author_name') or '同学',
        'tags': [t for t in (r.get('tags') or '').split(',') if t],
    } for r in rows], tags=[{'name': n, 'count': c} for n, c in top_tags])
