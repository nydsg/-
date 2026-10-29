"""
安全回归测试 —— 不需要 MySQL / torch / opencv / ffmpeg。

原理：在导入 zhanshizhan 之前，把重依赖（pymysql、models、utils、flask_sqlalchemy）
替换成内存桩，然后用 Flask 自带的 test_client 直接验证「鉴权 / 越权 / 入参」行为。

运行：
    cd C:\\Users\\联想\\Desktop\\xiangmu1
    python tests\\test_security.py

退出码 0 = 全部通过。
"""

import os
import sys
import types
import tempfile
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 测试状态：谁在审核员名单里
STATE = {'auditors': set(), 'queries': []}


def _handle(cur, sql, params):
    """极简 SQL 语义模拟：只为让被测代码走到我们关心的分支。"""
    low = ' '.join(sql.lower().split())
    params = params or ()
    if 'information_schema' in low:
        # 建表脚本会查 information_schema 判断列是否存在：返回 1 表示"已存在"，跳过 ALTER
        cur._rows = [(1,)]
    elif low.startswith('select count(*)'):
        cur._rows = [(10,)]
    elif 'from auditor' in low:
        uid = params[0] if params else None
        cur._rows = [(1,)] if uid in STATE['auditors'] else []
    elif low.startswith('select 1 from auditor'):
        uid = params[0] if params else None
        cur._rows = [(1,)] if uid in STATE['auditors'] else []
    elif 'from video_records' in low or 'from dianzan' in low or 'from lishi' in low \
            or 'from shoucang' in low or 'from users' in low or 'from noai_shenhe' in low:
        cur._rows = []
    elif low.startswith('insert') or low.startswith('update') or low.startswith('delete'):
        cur._rows = []
    else:
        cur._rows = []


class FakeCursor:
    def __init__(self, dict_mode=False):
        self.dict_mode = dict_mode
        self._rows = []
        self.lastrowid = 1
        self.description = None

    def execute(self, sql, params=None):
        STATE['queries'].append((sql, params))
        _handle(self, sql, params)
        return len(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.closed = False

    def cursor(self, *args):
        return FakeCursor(dict_mode=bool(args))

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


# ---------- 1. 替换 pymysql ----------
class _DictCursor:
    pass


fake_pymysql = types.ModuleType('pymysql')
fake_pymysql.connect = lambda **kwargs: FakeConn()
fake_pymysql.cursors = types.SimpleNamespace(DictCursor=_DictCursor)
sys.modules['pymysql'] = fake_pymysql

# ---------- 2. 替换 models ----------
class _FakeAuditor:
    def audit_video(self, frames, filename):
        return {'passed': True, 'score': 90, 'tags': [], 'violations': [],
                'confidence': 90, 'message': 'ok'}


class _FakeRecommender:
    def update_library(self, videos):
        self.videos = videos

    def recommend(self, query, top_k=10):
        return []


fake_models = types.ModuleType('models')
fake_models.VideoAuditor = _FakeAuditor
fake_models.Recommender = _FakeRecommender
sys.modules['models'] = fake_models

# ---------- 3. 替换 utils ----------
fake_utils = types.ModuleType('utils')
fake_utils.extract_frames = lambda path, max_frames=15: []
fake_utils.save_uploaded_video = lambda f, d: (os.path.join(d, 'x.mp4'), 'x.mp4')
fake_utils.delete_video = lambda p: None
fake_utils.StreamPusher = type('StreamPusher', (), {'start': lambda self, *a: True})
fake_utils.vitalframe = lambda path, folder: None
sys.modules['utils'] = fake_utils


# ---------- 4. 替换 flask_sqlalchemy ----------
class _Dummy:
    def __init__(self, *a, **k):
        pass


class _BaseModel:
    pass


class _Func:
    def current_timestamp(self):
        return _Dummy()


class FakeSQLAlchemy:
    Model = _BaseModel
    func = _Func()

    def __init__(self, app=None):
        self.session = types.SimpleNamespace(
            add=lambda obj: None, commit=lambda: None, rollback=lambda: None)

    def Column(self, *a, **k):
        return _Dummy()

    def Integer(self, *a, **k):
        return _Dummy()

    def String(self, *a, **k):
        return _Dummy()

    def Float(self, *a, **k):
        return _Dummy()

    def TIMESTAMP(self, *a, **k):
        return _Dummy()

    def ForeignKey(self, *a, **k):
        return _Dummy()

    def create_all(self):
        pass


fake_sa = types.ModuleType('flask_sqlalchemy')
fake_sa.SQLAlchemy = FakeSQLAlchemy
sys.modules['flask_sqlalchemy'] = fake_sa

# ---------- 5. 在临时目录里导入（避免在项目里生成 uploads/） ----------
# Windows 控制台默认 GBK，主程序启动时会打印 emoji，需先切到 UTF-8 容错模式
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_TMP = tempfile.mkdtemp(prefix='xiangmu-sec-')
os.chdir(_TMP)

# 源码里把上传目录硬编码成 E:\项目\xiangmu\...，而本机没有 E 盘。
# 这里把 E: 开头的建目录请求重定向到临时目录，只为让模块能导入（路径问题不属于本次安全修复范围）。
_E_ROOT = os.path.join(_TMP, 'E_drive')
_real_makedirs = os.makedirs


def _redirected_makedirs(path, *args, **kwargs):
    if isinstance(path, str) and path[:2].upper() == 'E:':
        path = os.path.join(_E_ROOT, path[2:].lstrip('\\/').replace('\\', '/'))
    return _real_makedirs(path, *args, **kwargs)


os.makedirs = _redirected_makedirs

import zhanshizhan  # noqa: E402  （必须在桩替换之后导入）
from zhanshizhan import app, safe_filename, ensure_inside  # noqa: E402


class SecurityTest(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        STATE['auditors'] = set()

    # ---- 工具 ----
    def login(self, user_id, username='tester'):
        with self.client.session_transaction() as sess:
            sess['user_id'] = user_id
            sess['username'] = username

    def make_auditor(self, user_id):
        STATE['auditors'].add(user_id)

    # ================= 1. 审核接口鉴权 =================
    def test_reject_video_anonymous(self):
        r = self.client.post('/reject_video', data={'shenhe_id': 1})
        self.assertEqual(r.status_code, 401, '匿名用户不应能拒绝投稿')

    def test_reject_video_normal_user(self):
        self.login(100)
        r = self.client.post('/reject_video', data={'shenhe_id': 1})
        self.assertEqual(r.status_code, 403, '普通用户不应能拒绝投稿')

    def test_reject_video_auditor(self):
        self.login(200)
        self.make_auditor(200)
        r = self.client.post('/reject_video', data={'shenhe_id': 1})
        self.assertNotIn(r.status_code, (401, 403), '审核员应被放行')

    def test_tongguo_video_normal_user(self):
        self.login(100)
        r = self.client.post('/tongguo_video', data={'shenhe_id': 1})
        self.assertEqual(r.status_code, 403, '普通用户不应能让投稿过审')

    def test_tongguo_video_anonymous(self):
        r = self.client.post('/tongguo_video', data={'shenhe_id': 1})
        self.assertEqual(r.status_code, 401)

    def test_clean_bg_rejects_normal_user(self):
        self.login(100)
        r = self.client.post('/api/cleanAllInvalidFavBg')
        self.assertEqual(r.status_code, 403, '清理接口不应向普通用户开放')

    def test_clean_bg_is_not_get(self):
        r = self.client.get('/api/cleanAllInvalidFavBg')
        self.assertEqual(r.status_code, 405, '会删文件的接口不应是 GET')

    def test_videos_noai_requires_auditor(self):
        r = self.client.get('/api/videosNOAI')
        self.assertEqual(r.status_code, 401)
        self.login(100)
        r = self.client.get('/api/videosNOAI')
        self.assertEqual(r.status_code, 403, '待审队列是审核员数据')

    def test_noaishenhe_page_guarded(self):
        r = self.client.get('/NOAIshenhe')
        self.assertEqual(r.status_code, 302, '匿名访问审核台应被重定向')
        self.login(100)
        r = self.client.get('/NOAIshenhe')
        self.assertEqual(r.status_code, 302, '非审核员访问审核台应被踢回')

    def test_noaishenhe_single_guard(self):
        r = self.client.get('/NOAIshenhe/1')
        self.assertEqual(r.status_code, 401)
        self.login(100)
        r = self.client.get('/NOAIshenhe/1')
        self.assertEqual(r.status_code, 403)

    # ================= 2. IDOR（越权读他人数据） =================
    def test_liked_videos_idor(self):
        self.login(100)
        r = self.client.get('/api/user/liked-videos/101')
        self.assertEqual(r.status_code, 403, '不应能读他人点赞列表')

    def test_liked_videos_self_ok(self):
        self.login(100)
        r = self.client.get('/api/user/liked-videos/100')
        self.assertNotIn(r.status_code, (401, 403))

    def test_liked_videos_anonymous(self):
        r = self.client.get('/api/user/liked-videos/100')
        self.assertEqual(r.status_code, 401)

    def test_history_videos_idor(self):
        self.login(100)
        r = self.client.get('/api/user/history-videos/101')
        self.assertEqual(r.status_code, 403, '不应能读他人浏览历史')

    def test_history_videos_self_ok(self):
        self.login(100)
        r = self.client.get('/api/user/history-videos/100')
        self.assertNotIn(r.status_code, (401, 403))

    def test_shoucangjia_idor(self):
        self.login(100)
        r = self.client.get('/api/user/101/shoucangjia/默认收藏夹')
        self.assertEqual(r.status_code, 403, '不应能读他人收藏夹内容')

    # ================= 3. 状态变更接口方法收紧 =================
    def test_shenheyuan_get_not_allowed(self):
        r = self.client.get('/shenheyuan')
        self.assertEqual(r.status_code, 405, '成为审核员不应能用 GET 触发')

    def test_shenheyuan_post_anonymous(self):
        r = self.client.post('/shenheyuan')
        self.assertEqual(r.status_code, 401)

    def test_shenheyuan_post_logged_in(self):
        self.login(300)
        r = self.client.post('/shenheyuan')
        self.assertNotIn(r.status_code, (401, 405), '登录用户应能提交申请')

    # ================= 4. 上传：鉴权与文件名清洗 =================
    def test_upload_requires_login(self):
        r = self.client.post('/upload', data={})
        self.assertEqual(r.status_code, 401)

    def test_upload_noai_requires_login(self):
        r = self.client.post('/uploadNOAI', data={})
        self.assertEqual(r.status_code, 401)

    def test_safe_filename_blocks_traversal(self):
        cases = {
            '../../etc/passwd': 'passwd',
            '..\\..\\win.ini': 'win.ini',
            'a/b/c.mp4': 'c.mp4',
            'CON': '_CON',
            '简历终稿.mp4': '简历终稿.mp4',   # 中文必须保留
            '': 'file',
        }
        for raw, expect_contains in cases.items():
            got = safe_filename(raw)
            self.assertNotIn('/', got, f'{raw!r} 清洗后仍含路径分隔符: {got!r}')
            self.assertNotIn('\\', got, f'{raw!r} 清洗后仍含反斜杠: {got!r}')
            self.assertEqual(got, expect_contains, f'{raw!r} -> {got!r}（期望 {expect_contains!r}）')

    def test_safe_filename_strips_dangerous_chars(self):
        got = safe_filename('a<b>|c?.mp4')
        for ch in '<>|?':
            self.assertNotIn(ch, got)
        self.assertTrue(got.endswith('.mp4'))

    def test_ensure_inside(self):
        base = os.path.join(_TMP, 'root')
        self.assertTrue(ensure_inside(base, os.path.join(base, 'x', 'y.mp4')))
        self.assertFalse(ensure_inside(base, os.path.join(base, '..', 'evil.mp4')))
        self.assertFalse(ensure_inside(base, os.path.join(_TMP, 'other', 'evil.mp4')))

    # ================= 5. 会话与密钥 =================
    def test_session_cookie_flags(self):
        self.assertEqual(app.config.get('SESSION_COOKIE_SAMESITE'), 'Lax')
        self.assertTrue(app.config.get('SESSION_COOKIE_HTTPONLY'))

    def test_secret_key_not_hardcoded_default(self):
        self.assertNotEqual(app.config['SECRET_KEY'], 'your-secret-key-here')
        self.assertGreaterEqual(len(app.config['SECRET_KEY']), 32)

    def test_db_password_not_in_any_python_source(self):
        """口令不应出现在任何 .py 源文件里（改前是 config.py + zhanshizhan.py 各一份）。"""
        needle = '@Love' + '3344521@'
        offenders = []
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in ('.git', 'tests', '__pycache__', 'venv')]
            for name in files:
                if not name.endswith('.py'):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, encoding='utf-8', errors='ignore') as f:
                        if needle in f.read():
                            offenders.append(path)
                except OSError:
                    pass
        self.assertEqual(offenders, [], f'这些源码文件仍含明文数据库口令: {offenders}')

    # ================= 7. 复核后新增（挡 B1/B3 回归） =================
    def test_stream_start_requires_auditor(self):
        r = self.client.post('/api/stream/start', json={'video_path': 'C:\\Windows\\notepad.exe'})
        self.assertEqual(r.status_code, 401, '匿名不应能调用推流接口')
        self.login(100)
        r = self.client.post('/api/stream/start', json={'video_path': 'C:\\Windows\\notepad.exe'})
        self.assertEqual(r.status_code, 403, '非审核员不应能调用推流接口')

    def test_stream_start_disabled_by_default(self):
        self.login(200)
        self.make_auditor(200)
        r = self.client.post('/api/stream/start',
                             json={'video_path': 'C:\\Windows\\notepad.exe',
                                   'rtmp_url': 'rtmp://evil.example/x'})
        self.assertEqual(r.status_code, 403)
        self.assertIn('未启用', (r.get_json() or {}).get('error', ''), '默认应关闭推流功能')

    def test_shenheyuan_does_not_self_grant(self):
        """B3 回归：默认不可「满 30 天自动成为审核员」。"""
        self.login(300)
        STATE['queries'] = []
        self.client.post('/shenheyuan')
        inserts = [q for q, _ in STATE['queries'] if 'insert into auditor' in ' '.join(q.lower().split())]
        self.assertEqual(inserts, [], f'默认不应直接写入 auditor 表，实际执行了: {inserts}')

    def test_audit_requires_login(self):
        r = self.client.post('/api/audit', data={})
        self.assertEqual(r.status_code, 401, 'AI 审核接口不应匿名可用')

    def test_dynamics_content_length_limited(self):
        self.login(100)
        r = self.client.post('/api/dynamics', data={'content': 'x' * 5000})
        self.assertEqual(r.status_code, 400, '超长动态正文应被拒绝')

    def test_noaiuploads_requires_login(self):
        r = self.client.get('/NOAIuploads/whatever.mp4')
        self.assertEqual(r.status_code, 401, '待审投稿不应匿名可下载')

    def test_video_status_whitelist(self):
        self.login(100)
        r = self.client.post('/api/video/1/status', json={'status': 'superadmin'})
        self.assertEqual(r.status_code, 400, '状态值应受白名单限制')

    # ================= 8. 可移植性（路径不再写死盘符） =================
    def test_no_hardcoded_drive_paths(self):
        """源码里不应再出现 `E:\\项目\\...` 这类绝对路径。"""
        offenders = []
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in ('.git', '.venv', 'venv',
                                                    '__pycache__', 'tests')]
            for name in files:
                if not name.endswith(('.py', '.html', '.js')):
                    continue
                path = os.path.join(root, name)
                try:
                    with open(path, encoding='utf-8', errors='ignore') as f:
                        text = f.read()
                except OSError:
                    continue
                for pattern in ('E:' + '\\项目', 'E:/' + '项目', 'localhost:5000/'):
                    if pattern in text:
                        offenders.append(f'{path}: {pattern}')
        self.assertEqual(offenders, [], f'仍有写死的绝对路径/地址: {offenders}')

    def test_media_url_is_relative_by_default(self):
        self.assertEqual(zhanshizhan.media_url('uploads/a/b.mp4'), '/uploads/a/b.mp4')
        self.assertEqual(zhanshizhan.media_url('/static/jpg/c.jpg'), '/static/jpg/c.jpg')

    def test_media_url_respects_public_base(self):
        old = app.config.get('PUBLIC_BASE_URL')
        app.config['PUBLIC_BASE_URL'] = 'https://cdn.example.com'
        try:
            self.assertEqual(zhanshizhan.media_url('uploads/a.mp4'),
                             'https://cdn.example.com/uploads/a.mp4')
        finally:
            app.config['PUBLIC_BASE_URL'] = old

    def test_config_dirs_are_under_base(self):
        import config as cfg
        base = cfg.BASE_DIR
        for name in ('UPLOAD_DIR', 'NOAI_DIR', 'AVATAR_DIR', 'BG_DIR',
                     'DYNAMIC_DIR', 'FAV_BG_DIR', 'CAROUSEL_DIR', 'JPG_DIR'):
            path = getattr(cfg.Config, name)
            self.assertTrue(os.path.abspath(path).startswith(base),
                            f'{name}={path} 不在项目根目录内')
            self.assertFalse(os.path.isabs(name))

    def test_healthz_responds(self):
        r = self.client.get('/healthz')
        self.assertIn(r.status_code, (200, 503), '/healthz 应始终可访问')
        body = r.get_json() or {}
        self.assertIn('db', body)
        self.assertIn('ai_auditor', body)

    # ================= 9. 前后端接口契约（不依赖真实库） =================
    def test_videos_api_selects_tags(self):
        """5 个分类页靠 v.tags 过滤，接口不返回 tags 就会恒空。"""
        STATE['queries'] = []
        self.client.get('/api/videos')
        sqls = ' '.join(q.lower() for q, _ in STATE['queries'])
        self.assertIn('tags', sqls, '/api/videos 的 SELECT 必须包含 tags')

    def test_videos_api_pagination(self):
        r = self.client.get('/api/videos?page=2&size=5')
        self.assertEqual(r.status_code, 200)
        body = r.get_json() or {}
        self.assertEqual(body.get('page'), 2)
        self.assertEqual(body.get('size'), 5)
        self.assertIn('has_more', body)
        self.assertIn('total', body)

    def test_videos_api_without_pagination_keeps_old_shape(self):
        r = self.client.get('/api/videos')
        body = r.get_json() or {}
        self.assertIn('video', body, '不传分页参数时应保持原有 {video: [...]} 结构')
        self.assertNotIn('page', body)

    def test_goto_redirects_to_index(self):
        """6 个个人主页模板顶栏写着 /goto，后端原先没有这个路由（404）。"""
        r = self.client.get('/goto')
        self.assertEqual(r.status_code, 302)
        self.assertIn('/index', r.headers.get('Location', ''))

    def test_video_detail_hides_server_path(self):
        STATE['queries'] = []
        self.client.get('/api/video/1')
        sqls = ' '.join(q.lower() for q, _ in STATE['queries'])
        self.assertNotIn('select *', sqls, '详情接口不应 SELECT *')
        self.assertNotIn('zuopin_path', sqls, '不应把服务器绝对路径返回给前端')

    def test_404_api_returns_json(self):
        r = self.client.get('/api/no-such-endpoint')
        self.assertEqual(r.status_code, 404)
        self.assertIn('application/json', r.headers.get('Content-Type', ''))
        self.assertEqual((r.get_json() or {}).get('error'), '接口不存在')

    def test_404_page_returns_html(self):
        r = self.client.get('/no-such-page')
        self.assertEqual(r.status_code, 404)
        self.assertIn('text/html', r.headers.get('Content-Type', ''))

    # ================= 6. 搜索入参 =================
    def test_search_top_k_clamped(self):
        for bad in ('999999', '-5', 'abc', None, '10'):
            r = self.client.post('/api/search', json={'query': 'x', 'top_k': bad})
            self.assertEqual(r.status_code, 200, f'top_k={bad!r} 不应导致 500')


def main():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(SecurityTest)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
