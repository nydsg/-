"""互动：点赞 / 收藏 / 评论 / 关注 / 通知 / 历史。"""
import logging

from flask import Blueprint, request, session

from ..db import use_cursor
from ..security import body, fail, is_authenticated, login_required, ok, paging

logger = logging.getLogger(__name__)
bp = Blueprint('social', __name__)


# ---------------- 点赞 ----------------
@bp.get('/videos/<int:video_id>/like')
def like_status(video_id: int):
    if not is_authenticated():
        return ok(liked=False, count=_count('dianzan', video_id))
    with use_cursor() as (_c, cur):
        cur.execute('SELECT 1 FROM dianzan WHERE user_id = %s AND video_id = %s',
                    (session['user_id'], video_id))
        liked = cur.fetchone() is not None
    return ok(liked=liked, count=_count('dianzan', video_id))


@bp.post('/videos/<int:video_id>/like')
@login_required
def toggle_like(video_id: int):
    uid = session['user_id']
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT id FROM dianzan WHERE user_id = %s AND video_id = %s',
                    (uid, video_id))
        exist = cur.fetchone()
        if exist:
            cur.execute('DELETE FROM dianzan WHERE id = %s', (exist['id'],))
            liked = False
        else:
            cur.execute('INSERT INTO dianzan (user_id, video_id) VALUES (%s,%s)',
                        (uid, video_id))
            liked = True
    return ok(liked=liked, count=_count('dianzan', video_id))


# ---------------- 收藏 ----------------
@bp.get('/videos/<int:video_id>/collect')
def collect_status(video_id: int):
    folder = request.args.get('folder') or '默认收藏夹'
    if not is_authenticated():
        return ok(collected=False)
    with use_cursor() as (_c, cur):
        cur.execute('SELECT 1 FROM shoucang WHERE user_id = %s AND video_id = %s '
                    'AND SCname = %s', (session['user_id'], video_id, folder))
        return ok(collected=cur.fetchone() is not None)


@bp.post('/videos/<int:video_id>/collect')
@login_required
def toggle_collect(video_id: int):
    uid = session['user_id']
    folder = (body().get('folder') or '默认收藏夹').strip() or '默认收藏夹'
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT id FROM shoucang WHERE user_id = %s AND video_id = %s '
                    'AND SCname = %s', (uid, video_id, folder))
        exist = cur.fetchone()
        if exist:
            cur.execute('DELETE FROM shoucang WHERE id = %s', (exist['id'],))
            collected, message = False, '已取消收藏'
        else:
            cur.execute('INSERT INTO shoucang (user_id, video_id, SCname) '
                        'VALUES (%s,%s,%s)', (uid, video_id, folder))
            collected, message = True, '收藏成功'
    return ok(collected=collected, message=message)


@bp.get('/folders')
@login_required
def folders():
    uid = session['user_id']
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT SCname AS name, COUNT(*) AS c FROM shoucang
            WHERE user_id = %s GROUP BY SCname ORDER BY c DESC
        """, (uid,))
        rows = cur.fetchall() or []
    if not rows:
        rows = [{'name': '默认收藏夹', 'c': 0}]
    return ok(items=[{'name': r['name'], 'count': r['c']} for r in rows])


@bp.get('/folders/<path:name>/videos')
@login_required
def folder_videos(name: str):
    uid = session['user_id']
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT v.id, v.zuopin_name, v.cover_path, v.view_count, v.zuopin_url,
                   u.name AS author_name
            FROM shoucang s JOIN video_records v ON s.video_id = v.id
            LEFT JOIN users u ON v.user_id = u.id
            WHERE s.user_id = %s AND s.SCname = %s
            ORDER BY s.created_at DESC
        """, (uid, name))
        rows = cur.fetchall() or []
    return ok(items=[{
        'id': r['id'], 'title': r.get('zuopin_name') or '未命名',
        'cover': r.get('cover_path') or '/static/jpg/home.jpg',
        'url': r.get('zuopin_url') or '',
        'views': r.get('view_count') or 0,
        'author': r.get('author_name') or '同学',
    } for r in rows])


# ---------------- 评论 ----------------
@bp.get('/videos/<int:video_id>/comments')
def comments(video_id: int):
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT c.id, c.user_id, c.username, c.name, c.content, c.parent_id,
                   c.reply_to_name, c.created_at, u.touxiang_url AS avatar
            FROM comments c LEFT JOIN users u ON c.user_id = u.id
            WHERE c.video_id = %s
            ORDER BY c.id ASC
        """, (video_id,))
        rows = cur.fetchall() or []

    nodes: dict[int, dict] = {}
    roots: list[dict] = []
    for r in rows:
        node = {
            'id': r['id'], 'content': r['content'],
            'author': r.get('name') or r.get('username') or '匿名',
            'user_id': r.get('user_id'), 'avatar': r.get('avatar') or '',
            'created_at': str(r.get('created_at') or ''),
            'reply_to': r.get('reply_to_name') or '',
            'replies': [],
        }
        nodes[r['id']] = node
        parent = r.get('parent_id') or 0
        if parent and parent in nodes:
            nodes[parent]['replies'].append(node)
        else:
            roots.append(node)
    return ok(items=roots, total=len(rows))


@bp.post('/videos/<int:video_id>/comments')
@login_required
def add_comment(video_id: int):
    data = body()
    content = (data.get('content') or '').strip()
    parent_id = data.get('parent_id') or 0
    if not content:
        return fail('评论内容不能为空')
    if len(content) > 600:
        return fail('评论最多 600 字')

    uid = session['user_id']
    reply_to_name = ''
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT id FROM video_records WHERE id = %s', (video_id,))
        if not cur.fetchone():
            return fail('作品不存在', 404)

        # 父评论归属校验：防伪造"回复@某人"通知、防跨视频串楼
        if parent_id:
            cur.execute('SELECT id, video_id, user_id, username, name '
                        'FROM comments WHERE id = %s', (parent_id,))
            parent = cur.fetchone()
            if not parent:
                return fail('被回复的评论不存在', 404)
            if int(parent['video_id']) != int(video_id):
                return fail('评论与作品不匹配')
            reply_to_name = parent.get('name') or parent.get('username') or ''
            reply_to_uid = parent.get('user_id')
            reply_to_username = parent.get('username') or ''
        else:
            reply_to_uid = reply_to_username = None

        cur.execute("""
            INSERT INTO comments
              (video_id, user_id, username, name, content, parent_id,
               reply_to_user_id, reply_to_username, reply_to_name)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (video_id, uid, session.get('username'), session.get('name'),
              content, parent_id or 0, reply_to_uid, reply_to_username, reply_to_name))
        comment_id = cur.lastrowid

        # 通知（顶级评论通知作者，回复通知被回复者）
        cur.execute('SELECT user_id, zuopin_name FROM video_records WHERE id = %s',
                    (video_id,))
        video = cur.fetchone() or {}
        target = reply_to_uid if parent_id else video.get('user_id')
        if target and target != uid and ('comment' if not parent_id else 'reply'):
            cur.execute("""
                INSERT INTO notifications
                  (user_id, type, sender_id, sender_name, video_id, video_title, comment_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (target, 'reply' if parent_id else 'comment', uid,
                  session.get('name'), video_id, video.get('zuopin_name'), comment_id))

    return ok(id=comment_id, message='评论成功')


@bp.delete('/comments/<int:comment_id>')
@login_required
def delete_comment(comment_id: int):
    uid = session['user_id']
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT user_id FROM comments WHERE id = %s', (comment_id,))
        row = cur.fetchone()
        if not row:
            return fail('评论不存在', 404)
        if row['user_id'] != uid:
            return fail('无权删除此评论', 403)
        # parent_id 没有外键级联，必须显式删子回复（否则留下孤儿）
        cur.execute('DELETE FROM comments WHERE parent_id = %s', (comment_id,))
        cur.execute('DELETE FROM comments WHERE id = %s', (comment_id,))
    return ok(message='已删除')


# ---------------- 关注 ----------------
@bp.post('/users/<int:user_id>/follow')
@login_required
def toggle_follow(user_id: int):
    uid = session['user_id']
    if uid == user_id:
        return fail('不能关注自己')
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT id FROM follows WHERE follower_id = %s AND followed_id = %s',
                    (uid, user_id))
        exist = cur.fetchone()
        if exist:
            cur.execute('DELETE FROM follows WHERE id = %s', (exist['id'],))
            following = False
        else:
            cur.execute('INSERT INTO follows (follower_id, followed_id) VALUES (%s,%s)',
                        (uid, user_id))
            following = True
    return ok(following=following, fans=_count_followers(user_id))


@bp.get('/users/<int:user_id>/follow')
def follow_status(user_id: int):
    if not is_authenticated():
        return ok(following=False, fans=_count_followers(user_id), following_count=0)
    with use_cursor() as (_c, cur):
        cur.execute('SELECT 1 FROM follows WHERE follower_id = %s AND followed_id = %s',
                    (session['user_id'], user_id))
        following = cur.fetchone() is not None
        cur.execute('SELECT COUNT(*) AS c FROM follows WHERE follower_id = %s',
                    (user_id,))
        following_count = (cur.fetchone() or {}).get('c', 0)
    return ok(following=following, fans=_count_followers(user_id),
              following_count=following_count)


# ---------------- 通知 ----------------
@bp.get('/notifications')
@login_required
def notifications():
    uid = session['user_id']
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT id, type, sender_name, video_id, video_title, comment_content,
                   is_read, created_at
            FROM notifications WHERE user_id = %s
            ORDER BY created_at DESC LIMIT 50
        """, (uid,))
        rows = cur.fetchall() or []
        cur.execute('SELECT COUNT(*) AS c FROM notifications '
                    'WHERE user_id = %s AND is_read = 0', (uid,))
        unread = (cur.fetchone() or {}).get('c', 0)
    return ok(items=[{
        'id': r['id'], 'type': r['type'], 'sender': r.get('sender_name') or '系统',
        'video_id': r.get('video_id'), 'title': r.get('video_title') or '',
        'content': r.get('comment_content') or '', 'read': bool(r.get('is_read')),
        'created_at': str(r.get('created_at') or ''),
    } for r in rows], unread=unread)


@bp.post('/notifications/read')
@login_required
def read_notifications():
    uid = session['user_id']
    nid = body().get('id')
    with use_cursor(commit=True) as (_c, cur):
        if nid:
            cur.execute('UPDATE notifications SET is_read = 1 '
                        'WHERE id = %s AND user_id = %s', (nid, uid))
        else:
            cur.execute('UPDATE notifications SET is_read = 1 WHERE user_id = %s', (uid,))
    return ok(message='已标记已读')


# ---------------- 浏览历史 ----------------
@bp.get('/history')
@login_required
def history():
    uid = session['user_id']
    with use_cursor() as (_c, cur):
        cur.execute("""
            SELECT v.id, v.zuopin_name, v.cover_path, v.zuopin_url, v.view_count,
                   u.name AS author_name, l.created_at
            FROM lishi l JOIN video_records v ON l.video_id = v.id
            LEFT JOIN users u ON v.user_id = u.id
            WHERE l.user_id = %s ORDER BY l.created_at DESC LIMIT 50
        """, (uid,))
        rows = cur.fetchall() or []
    return ok(items=[{
        'id': r['id'], 'title': r.get('zuopin_name') or '未命名',
        'cover': r.get('cover_path') or '/static/jpg/home.jpg',
        'url': r.get('zuopin_url') or '', 'views': r.get('view_count') or 0,
        'author': r.get('author_name') or '同学',
        'watched_at': str(r.get('created_at') or ''),
    } for r in rows])


@bp.post('/history')
@login_required
def add_history():
    uid = session['user_id']
    vid = body().get('video_id')
    if not vid:
        return fail('缺少 video_id')
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('DELETE FROM lishi WHERE user_id = %s AND video_id = %s', (uid, vid))
        cur.execute('INSERT INTO lishi (user_id, video_id) VALUES (%s,%s)', (uid, vid))
        cur.execute("""
            DELETE FROM lishi WHERE user_id = %s AND id NOT IN (
                SELECT id FROM (SELECT id FROM lishi WHERE user_id = %s
                                ORDER BY created_at DESC LIMIT 50) t)
        """, (uid, uid))
    return ok()


@bp.delete('/history')
@login_required
def clear_history():
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('DELETE FROM lishi WHERE user_id = %s', (session['user_id'],))
    return ok(message='已清空')


# ---------------- 工具 ----------------
def _count(table: str, video_id: int) -> int:
    with use_cursor() as (_c, cur):
        cur.execute(f'SELECT COUNT(*) AS c FROM {table} WHERE video_id = %s', (video_id,))
        return (cur.fetchone() or {}).get('c', 0)


def _count_followers(user_id: int) -> int:
    with use_cursor() as (_c, cur):
        cur.execute('SELECT COUNT(*) AS c FROM follows WHERE followed_id = %s', (user_id,))
        return (cur.fetchone() or {}).get('c', 0)
