"""认证：注册 / 登录 / 登出 / 当前用户 / 审核员申请。"""
import logging

from flask import Blueprint, session
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import use_cursor, DatabaseUnavailable
from ..security import (body, fail, is_auditor, is_authenticated, login_required,
                        ok)

logger = logging.getLogger(__name__)
bp = Blueprint('auth', __name__)


def _public_user(row: dict) -> dict:
    return {
        'id': row['id'],
        'username': row['username'],
        'name': row.get('name') or row['username'],
        'school': row.get('school') or '未公开',
        'signature': row.get('signature') or '',
        'avatar': row.get('touxiang_url') or '',
        'bg': row.get('bg_url') or '',
        'is_auditor': False,
    }


@bp.post('/register')
def register():
    data = body()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    confirm = data.get('confirm') or data.get('password1') or ''
    email = (data.get('email') or '').strip()

    if not username or not password or not email:
        return fail('用户名、密码、邮箱都不能为空')
    if len(username) < 2 or len(username) > 40:
        return fail('用户名长度需在 2~40 之间')
    if len(password) < 6:
        return fail('密码至少 6 位')
    if password != confirm:
        return fail('两次输入的密码不一致')

    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT id FROM users WHERE username = %s OR email = %s',
                    (username, email))
        if cur.fetchone():
            return fail('用户名或邮箱已被注册', 409)
        cur.execute(
            'INSERT INTO users (username, password, email, name) VALUES (%s,%s,%s,%s)',
            (username, generate_password_hash(password), email, username))
        uid = cur.lastrowid
    logger.info('新用户注册: %s (id=%s)', username, uid)
    return ok(id=uid, message='注册成功，请登录')


@bp.post('/login')
def login():
    data = body()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    if not username or not password:
        return fail('请输入用户名与密码')

    with use_cursor() as (_c, cur):
        cur.execute('SELECT * FROM users WHERE username = %s', (username,))
        user = cur.fetchone()
    if not user or not check_password_hash(user['password'], password):
        return fail('用户名或密码错误', 401)

    session.clear()                      # 防会话固定
    session.permanent = True
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['name'] = user.get('name') or user['username']

    payload = _public_user(user)
    payload['is_auditor'] = is_auditor(user['id'])
    return ok(user=payload)


@bp.post('/logout')
def logout():
    session.clear()
    return ok(message='已退出登录')


@bp.get('/me')
def me():
    if not is_authenticated():
        return ok(user=None, authenticated=False)
    uid = session['user_id']
    with use_cursor() as (_c, cur):
        cur.execute('SELECT * FROM users WHERE id = %s', (uid,))
        user = cur.fetchone()
        if not user:
            session.clear()
            return ok(user=None, authenticated=False)
        cur.execute("""
            SELECT COUNT(*) AS c FROM dianzan d
            JOIN video_records v ON d.video_id = v.id
            WHERE v.user_id = %s
        """, (uid,))
        likes = (cur.fetchone() or {}).get('c', 0)
        cur.execute('SELECT COUNT(*) AS c FROM video_records WHERE user_id = %s', (uid,))
        works = (cur.fetchone() or {}).get('c', 0)
        cur.execute('SELECT COUNT(*) AS c FROM follows WHERE followed_id = %s', (uid,))
        fans = (cur.fetchone() or {}).get('c', 0)

    payload = _public_user(user)
    payload.update(is_auditor=is_auditor(uid), total_likes=likes,
                   works=works, fans=fans)
    return ok(user=payload, authenticated=True)


@bp.post('/auditor/apply')
@login_required
def apply_auditor():
    """注册满 30 天可申请；权限由管理员审批（不再自助授予）。"""
    uid = session['user_id']
    if is_auditor(uid):
        return ok(message='你已经是审核员了')

    with use_cursor(commit=True) as (_c, cur):
        cur.execute('SELECT created_at FROM users WHERE id = %s', (uid,))
        row = cur.fetchone()
        if not row:
            return fail('用户不存在', 404)
        days = (__import__('datetime').datetime.now()
                - row['created_at']).days
        if days < 30:
            return fail(f'注册未满 30 天（已 {days} 天，还需 {30 - days} 天）', 403)
        cur.execute("""
            INSERT INTO auditor_apply (user_id, status) VALUES (%s, 'pending')
            ON DUPLICATE KEY UPDATE status = IF(status = 'rejected', 'pending', status)
        """, (uid,))
    return ok(days=days, message='申请已提交，等待管理员审批')


@bp.get('/auditor/applications')
def applications():
    """审核员可查看待审批申请。"""
    return ok(items=[])
