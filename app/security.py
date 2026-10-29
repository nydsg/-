"""鉴权、入参校验、文件名清洗、统一错误响应。"""
import logging
import os
import re
from functools import wraps

from flask import jsonify, request, session

from .db import use_cursor, DatabaseUnavailable

logger = logging.getLogger(__name__)

_WINDOWS_RESERVED = {
    'CON', 'PRN', 'AUX', 'NUL',
    *{f'COM{i}' for i in range(1, 10)},
    *{f'LPT{i}' for i in range(1, 10)},
}
_UNSAFE = re.compile(r'[^0-9A-Za-z\u4e00-\u9fff._\- ]')


# ---------------- 响应助手 ----------------
def ok(**payload):
    return jsonify({'success': True, **payload})


def fail(message, status=400, **extra):
    return jsonify({'success': False, 'error': message, 'message': message, **extra}), status


# ---------------- 鉴权 ----------------
def current_user_id():
    return session.get('user_id')


def is_authenticated() -> bool:
    return 'user_id' in session


def is_auditor(user_id=None) -> bool:
    user_id = user_id or current_user_id()
    if not user_id:
        return False
    try:
        with use_cursor(dict_cursor=False) as (_c, cur):
            cur.execute('SELECT 1 FROM auditor WHERE user_id = %s', (user_id,))
            return cur.fetchone() is not None
    except DatabaseUnavailable:
        return False
    except Exception as e:                       # 逻辑上不该发生，但绝不因此放行
        logger.warning('审核员校验异常: %s', e)
        return False


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            return fail('请先登录', 401)
        return view(*args, **kwargs)
    return wrapper


def auditor_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_authenticated():
            return fail('请先登录', 401)
        if not is_auditor():
            logger.warning('越权拦截: user=%s path=%s',
                           session.get('username'), request.path)
            return fail('需要审核员权限', 403)
        return view(*args, **kwargs)
    return wrapper


# ---------------- 文件名 / 路径 ----------------
def safe_filename(name: str, fallback: str = 'file', max_stem: int = 80) -> str:
    """清洗用户文件名：只留 basename、去危险字符、规避 Windows 保留名，中文保留。"""
    name = os.path.basename(str(name or '').replace('\\', '/'))
    stem, ext = os.path.splitext(name)
    ext = _UNSAFE.sub('', ext)[:12]
    stem = _UNSAFE.sub('_', stem).strip('. ')
    if not stem:
        stem = fallback
    if stem.upper() in _WINDOWS_RESERVED:
        stem = f'_{stem}'
    return f'{stem[:max_stem]}{ext}'


def ensure_inside(base_dir: str, target: str) -> bool:
    base = os.path.abspath(base_dir)
    tgt = os.path.abspath(target)
    try:
        return os.path.commonpath([base, tgt]) == base
    except ValueError:
        return False


# ---------------- 入参 ----------------
def body() -> dict:
    return request.get_json(silent=True) or {}


def as_int(value, default=None, low=None, high=None):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    if low is not None:
        v = max(low, v)
    if high is not None:
        v = min(high, v)
    return v


def paging(default_size: int = 12, max_size: int = 60):
    """统一分页参数：?page=1&size=12"""
    page = as_int(request.args.get('page'), 1, 1, 10000)
    size = as_int(request.args.get('size'), default_size, 1, max_size)
    return page, size, (page - 1) * size
