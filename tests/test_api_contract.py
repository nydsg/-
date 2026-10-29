"""
新后端（app/ 包）的 API 契约测试 —— 不需要 MySQL。

做法：给 app.db._connect 打桩，返回可编排的假连接；再用 Flask test_client
逐条验证「响应结构是否与前端 TypeScript 类型一致」。
这是在没有数据库的情况下，验证前后端契约的最有效手段。

运行：.venv\\Scripts\\python.exe tests\\test_api_contract.py
"""
import io
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.db as app_db  # noqa: E402  必须在打桩前导入模块对象

PASS = 0
FAIL = 0
FAILURES = []


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  OK   {name}')
    else:
        FAIL += 1
        FAILURES.append(f'{name} {detail}')
        print(f'  FAIL {name}  {detail}')


# ---------------- 假数据库 ----------------
class FakeCursor:
    def __init__(self, rows, dict_mode):
        self._rows = rows
        self._dict = dict_mode
        self._pos = 0
        self.lastrowid = 1
        self.rowcount = 1

    def execute(self, sql, params=None):
        # 由 TEST_STATE['handler'] 决定返回什么
        self._rows = TEST_STATE['handler'](sql, params) or []
        self._pos = 0
        return len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConn:
    def __init__(self, dict_mode):
        self._dict = dict_mode

    def cursor(self, *a, **k):
        return FakeCursor([], self._dict)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


TEST_STATE = {'handler': lambda sql, params: [], 'dict': True, 'queries': []}


def fake_connect(dict_cursor=False):
    TEST_STATE['dict'] = dict_cursor
    return FakeConn(dict_cursor)


app_db._connect = fake_connect
app_db.ping = lambda: True

from app import create_app  # noqa: E402

app = create_app()
app.config.update(TESTING=True)


def cursor(*rows):
    """快速设定后续 SQL 返回的行。

    两个踩过的坑：
    1. 必须整体替换 handler，否则上个用例的回调残留 → 假阳性；
    2. `list(dict)` 得到的是 **key 列表**，传单个 dict 时必须包成 [dict]，
       否则下游 r['id'] 会抛 "list indices must be integers"。
    """
    # cursor([]) 的语义是"空结果集"，不是"含一个空列表的结果集"；
    # list([]) == [] 是对的，但 list(([],)) == [[]] 会把空列表当成一行数据。
    if len(rows) == 1 and isinstance(rows[0], (list, tuple)):
        payload = list(rows[0])
    elif len(rows) == 1 and isinstance(rows[0], dict):
        payload = [rows[0]]
    else:
        payload = list(rows)

    def handler(sql, params):
        TEST_STATE['queries'].append((' '.join(sql.split()), params))
        return list(payload)
    TEST_STATE['handler'] = handler


def reset():
    """恢复为"所有查询返回空集"，避免用例间互相污染。"""
    TEST_STATE['queries'] = []
    TEST_STATE['handler'] = lambda sql, params: []


def client():
    c = app.test_client()
    with c.session_transaction() as s:
        s['user_id'] = 1
        s['username'] = 'demo'
        s['name'] = '演示用户'
    return c


# ============ 1. 鉴权 ============
reset()
print('== 1. 鉴权契约 ==')
TEST_STATE['handler'] = lambda sql, params: []
r = app.test_client().get('/api/auth/me')
body = r.get_json()
check('匿名 /auth/me 返回 200 且 user=null',
      r.status_code == 200 and body.get('user') is None and body.get('authenticated') is False,
      f'status={r.status_code} body={body}')

r = app.test_client().post('/api/auth/login', json={'username': 'x', 'password': 'y'})
check('登录失败返回 401 + error 字段',
      r.status_code == 401 and r.get_json().get('success') is False and r.get_json().get('error'),
      f'{r.status_code} {r.get_json()}')

r = app.test_client().post('/api/auth/register', json={'username': '', 'password': '', 'email': ''})
check('注册缺参数返回 400', r.status_code == 400, f'{r.status_code}')

# ============ 2. 作品 ============
reset()
print('== 2. 作品契约 ==')
cursor({
    'id': 7, 'user_id': 1, 'zuopin_name': '图书馆的一天', 'zuopin_url': '/uploads/a.mp4',
    'cover_path': '/uploads/a.jpg', 'file_size': 1024, 'upload_time': None,
    'view_count': 42, 'tags': '校园,日常', 'username': 'demo', 'author_name': '演示用户',
    'author_avatar': '/static/avatars/x.jpg',
})
r = app.test_client().get('/api/videos?page=1&size=12')
b = r.get_json()
check('/videos 200', r.status_code == 200, f'{r.status_code}')
check('/videos 返回分页字段 items/total/page/size/has_more',
      all(k in b for k in ('items', 'total', 'page', 'size', 'has_more')), f'keys={list(b)[:8]}')
item = (b.get('items') or [{}])[0]
check('/videos item 字段与前端 VideoCard 一致',
      all(k in item for k in ('id', 'title', 'url', 'cover', 'views', 'tags', 'author')),
      f'item keys={list(item)}')
check('tags 已解析为数组', isinstance(item.get('tags'), list) and item['tags'] == ['校园', '日常'],
      f"tags={item.get('tags')}")

TEST_STATE['handler'] = lambda sql, params: []
r = app.test_client().get('/api/videos/999')
check('不存在的作品返回 404', r.status_code == 404, f'{r.status_code}')

# ============ 3. 互动鉴权 ============
reset()
print('== 3. 互动接口鉴权 ==')
anon = app.test_client()
for path, method in (('/api/videos/1/like', 'post'), ('/api/videos/1/collect', 'post'),
                     ('/api/videos/1/comments', 'post'), ('/api/videos/1/view', 'post')):
    r = getattr(anon, method)(path, json={})
    expect_401 = path != '/api/videos/1/view'
    if expect_401:
        check(f'匿名 {path} 需登录(401/400)', r.status_code in (400, 401, 403), f'{r.status_code}')
    else:
        check(f'匿名 {path} 允许（播放量）', r.status_code in (200, 503), f'{r.status_code}')

r = anon.get('/api/videos/1/like')
check('匿名可读点赞状态', r.status_code == 200 and 'liked' in (r.get_json() or {}),
      f'{r.status_code} {r.get_json()}')

# ============ 4. 搜索 / 推荐流 ============
reset()
print('== 4. 搜索与推荐流 ==')
cursor([])
r = app.test_client().get('/api/search?q=图书馆')
b = r.get_json()
check('/search 200 且含 items/users/keyword',
      r.status_code == 200 and all(k in b for k in ('items', 'users', 'keyword')), f'{list(b)[:6]}')

r = app.test_client().get('/api/shorts')
b = r.get_json()
check('/shorts 含 items/tags',
      r.status_code == 200 and 'items' in b and 'tags' in b, f'{list(b)[:6]}')

# ============ 5. 个人主页 ============
reset()
print('== 5. 个人主页 ==')
TEST_STATE['handler'] = lambda sql, params: []
r = app.test_client().get('/api/users/1')
check('不存在的用户 404', r.status_code == 404, f'{r.status_code}')

# ============ 6. 审核台鉴权 ============
reset()
print('== 6. 审核台 ==')
r = app.test_client().get('/api/review/queue')
check('匿名访问审核队列 401', r.status_code == 401, f'{r.status_code}')

TEST_STATE['handler'] = lambda sql, params: []
r = client().get('/api/review/queue')
check('非审核员访问审核队列 403', r.status_code == 403, f'{r.status_code}')

# 变成审核员
def auditor_handler(sql, params):
    if 'from auditor' in sql.lower():
        return [(1,)]
    return []
TEST_STATE['handler'] = auditor_handler
r = client().get('/api/review/queue')
check('审核员访问队列 200 且含 items/ai_runs',
      r.status_code == 200 and all(k in (r.get_json() or {}) for k in ('items', 'ai_runs')),
      f'{r.status_code} {list((r.get_json() or {}))[:5]}')

# ============ 7. AI 契约 ============
reset()
print('== 7. AI 接口 ==')
TEST_STATE['handler'] = lambda sql, params: []
r = app.test_client().get('/api/ai/status')
b = r.get_json()
check('/ai/status 含 ai_enabled/model/rag_docs/capabilities',
      all(k in b for k in ('ai_enabled', 'model', 'rag_docs', 'capabilities')), f'{list(b)[:8]}')

r = app.test_client().get('/api/ai/rag/search?q=成人礼&k=3')
b = r.get_json()
check('/ai/rag/search 命中礼仪豁免条款 R2-2',
      r.status_code == 200 and any(h['id'] == 'R2-2' for h in b.get('items', [])),
      f"ids={[h['id'] for h in b.get('items', [])]}")
check('RAG 返回项含 id/title/text/cat/score/source',
      all(k in (b['items'][0] if b.get('items') else {})
          for k in ('id', 'title', 'text', 'cat', 'score', 'source')), '字段缺失')

r = app.test_client().post('/api/ai/chat', json={})
check('/ai/chat 空消息 400', r.status_code == 400, f'{r.status_code}')

r = app.test_client().post('/api/ai/tags', json={'title': '图书馆的一天'})
b = r.get_json()
check('/ai/tags 无 key 时返回兜底标签',
      r.status_code == 200 and isinstance(b.get('tags'), list) and len(b['tags']) > 0,
      f'{b}')

r = app.test_client().post('/api/ai/audit/stream', json={'filename': 'x.mp4'})
check('匿名审核流 401', r.status_code == 401, f'{r.status_code}')

# ============ 8. 上传校验 ============
reset()
print('== 8. 上传校验 ==')
data = {'file': (io.BytesIO(b'not a video'), 'evil.exe')}
r = client().post('/api/videos/upload', data=data, content_type='multipart/form-data')
check('非法扩展名被拒 400', r.status_code == 400, f'{r.status_code} {r.get_json()}')

# ============ 9. 错误处理 ============
reset()
print('== 9. 统一错误处理 ==')
r = app.test_client().get('/api/no-such-endpoint')
check('未知 API 返回 404 JSON',
      r.status_code == 404 and (r.get_json() or {}).get('error'), f'{r.status_code}')

r = app.test_client().get('/some-page')
check('未知页面返回 SPA 入口（200 html）',
      r.status_code == 200 and 'text/html' in r.headers.get('Content-Type', ''),
      f"{r.status_code} {r.headers.get('Content-Type')}")

TEST_STATE['handler'] = lambda sql, params: []
r = app.test_client().get('/api/health')
check('/api/health 返回 db/ai/spa 字段',
      all(k in (r.get_json() or {}) for k in ('db', 'ai', 'spa')), f'{r.get_json()}')

print()
print(f'结果：{PASS} 通过 / {FAIL} 失败')
if FAILURES:
    print('失败项：')
    for f in FAILURES:
        print('  -', f)
sys.exit(0 if FAIL == 0 else 1)
