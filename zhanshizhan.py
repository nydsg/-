from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import pymysql
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
import re
import time
import uuid
import shutil
import logging
import secrets
from datetime import datetime
from functools import wraps
from urllib.parse import quote_plus
from flask import send_from_directory
# 导入配置
from config import DevelopmentConfig, ensure_dirs

# 导入AI模块
from models import VideoAuditor, Recommender
from utils import extract_frames, save_uploaded_video, delete_video, StreamPusher,vitalframe

# ===== 启动兼容性修复 =====
# Windows 控制台默认 GBK，而下面的启动日志含 ✅/⚠️ 等字符，print 会抛 UnicodeEncodeError，
# 且它发生在 try/except 内部，会把「AI 模块初始化」的异常处理也一起打断，导致导入即失败。
# 先把标准输出切到 UTF-8 容错模式，保证 `python zhanshizhan.py` 能正常启动。
try:
    import sys as _sys
    _sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    _sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.config.from_object(DevelopmentConfig)
app.secret_key = app.config['SECRET_KEY']
#session配置
app.config['SESSION_PERMANENT']=True
app.config['SESSION_TYPE']='filesystem'
app.config['PERMANENT_SESSION_LIFETIME']=3600*24*7
# ===== 安全加固：会话 Cookie 属性 =====
# SameSite=Lax 让浏览器在跨站 POST 请求中不携带会话 Cookie，是抵御 CSRF 的第一道防线
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY'] = True
# 站点启用 HTTPS 后把 SESSION_COOKIE_SECURE 设为 1（或用环境变量），让 Cookie 只在加密链路传输
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
# 上传配置
app.config['UPLOAD_FOLDER'] = app.config.get('UPLOAD_FOLDER', 'uploads')
app.config['MAX_CONTENT_LENGTH'] = app.config.get('MAX_CONTENT_LENGTH', 500 * 1024 * 1024)

# 创建上传文件夹
ensure_dirs()      # 建好 uploads / NOAIuploads / avatars / backgrounds / dynamics 等目录
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 数据库配置
# 安全修复：不再在这里重复一份带明文口令的连接串；直接取 config.py 里由 DB_CONFIG 派生的 URI
if not app.config.get('SQLALCHEMY_DATABASE_URI'):
    _dbc = app.config.get('DB_CONFIG') or {}
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        f"mysql+pymysql://{_dbc.get('user', 'root')}:"
        f"{quote_plus(str(_dbc.get('password', '')))}"
        f"@{_dbc.get('host', 'localhost')}:{_dbc.get('port', 3306)}"
        f"/{_dbc.get('database', 'userdb')}"
    )
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
# 日志配置
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

#"""从数据库加载视频到推荐器"""
def load_videos_to_recommender():
    """从数据库加载视频到推荐器。

    修复：原实现只要数据库或推荐器任一不可用，就会在**导入期**抛 AttributeError，
    导致整个网站起不来（数据库没起来 → 网站也起不来）。现在改为跳过并告警。
    """
    if recommender is None:
        print("⚠️ 推荐系统未初始化，跳过视频库加载（搜索将降级为 SQL LIKE）")
        return
    conn = get_db()
    if not conn:
        print("⚠️ 数据库不可用，跳过视频库加载（稍后可重启服务重建索引）")
        return
    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute('''
            SELECT id, zuopin_name, tags, view_count, cover_path, zuopin_url, username
            FROM video_records
        ''')
        videos = cursor.fetchall()
        cursor.close()
        conn.close()
        recommender.update_library(videos)
        print(f"推荐器加载了 {len(videos)} 个视频")
    except Exception as e:
        print(f"⚠️ 推荐器加载失败（不影响主服务）: {e}")
        try:
            conn.close()
        except Exception:
            pass
# 初始化AI模块 
print("=" * 50)
print("正在初始化AI模块...")
print("=" * 50)

try:
    video_auditor = VideoAuditor()
    # 修复：模型没加载成功时 object 依然能构造出来，若直接当成"可用"，
    # 会用兜底分把每一次投稿都判成"审核不通过"。这里显式判定就绪状态。
    if not getattr(video_auditor, 'ready', True):
        print("⚠️ AI 模型未就绪（缺 CLIP / EasyOCR 依赖），AI 审核通道将降级为人工审核")
        video_auditor = None
    else:
        print("✅ 视频审核模块初始化成功")
except Exception as e:
    print(f"⚠️ 视频审核模块初始化失败: {e}")
    video_auditor = None

try:
    recommender = Recommender()
    print("✅ 推荐系统模块初始化成功")
except Exception as e:
    print(f"⚠️ 推荐系统模块初始化失败: {e}")
    recommender = None

print("=" * 50)
print("服务启动成功！")
print("=" * 50)

# ============= 数据库模型 =============
class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(120))
    name = db.Column(db.String(100), nullable=False, default='')
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())


class VideoRecord(db.Model):
    """视频审核记录表"""
    __tablename__ = 'video_records'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    username = db.Column(db.String(80))
    zuopin_name = db.Column(db.String(255))
    audit_result = db.Column(db.String(50))
    violations = db.Column(db.String(500))
    confidence = db.Column(db.Float)
    created_at = db.Column(db.TIMESTAMP, server_default=db.func.current_timestamp())


# ============= 数据库函数 =============
def get_db():
    # 安全修复：口令只保留在 config.py 一处（支持环境变量），此处不再重复硬编码
    db_conf = app.config.get('DB_CONFIG')
    if not db_conf:
        logger.error("数据库配置缺失：请检查 config.py 的 DB_CONFIG")
        return None
    if not db_conf.get('password'):
        logger.error("数据库口令未配置：请设置环境变量 DB_PASSWORD，"
                     "或在项目根目录创建 .db_password 文件")
        return None
    try:
        conn = pymysql.connect(**db_conf)
        return conn
    except Exception as e:
        print(f"数据库连接失败：{e}")
        return None


# ============= 安全工具（鉴权 / 文件名清洗 / 路径校验） =============
_WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    *{f'COM{i}' for i in range(1, 10)},
    *{f'LPT{i}' for i in range(1, 10)},
}
_UNSAFE_FILENAME_CHARS = re.compile(r'[^0-9A-Za-z\u4e00-\u9fff._\- ]')


def safe_filename(name, fallback='file', max_stem=80):
    """把用户提供的文件名清洗成可安全落盘的「纯文件名」。

    用户上传的 filename 完全可信度为零，可能带 `../`、盘符、控制字符或 Windows
    保留设备名。这里只保留最后一段并过滤特殊字符，中文原样保留（避免
    werkzeug.secure_filename 把中文名清成空串而丢失作品名）。
    """
    # Windows 上 os.path.basename 同时处理 / 和 \，先统一分隔符更稳妥
    name = os.path.basename(str(name or '').replace('\\', '/'))
    stem, ext = os.path.splitext(name)
    ext = _UNSAFE_FILENAME_CHARS.sub('', ext)[:12]
    stem = _UNSAFE_FILENAME_CHARS.sub('_', stem).strip('. ')
    if not stem:
        stem = fallback
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f'_{stem}'
    return f'{stem[:max_stem]}{ext}'


def ensure_inside(base_dir, target_path):
    """确认 target_path 解析后仍位于 base_dir 之内，用于防目录穿越。"""
    base = os.path.abspath(base_dir)
    target = os.path.abspath(target_path)
    try:
        return os.path.commonpath([base, target]) == base
    except ValueError:      # 不同盘符，commonpath 会抛异常
        return False


def media_url(relative_path):
    """把站内相对路径转成对外 URL。

    修复前这里把带端口的主机名写死在返回值里，导致换端口、局域网访问、
    反向代理域名下一律黑屏。现在默认返回相对路径（`/uploads/xxx`），
    任何访问方式都正确；需要 CDN/绝对地址时设环境变量 PUBLIC_BASE_URL。
    """
    base = app.config.get('PUBLIC_BASE_URL') or ''
    rel = '/' + str(relative_path).lstrip('/')
    return f"{base}{rel}" if base else rel


def is_auditor(user_id=None):
    """判断当前（或指定）用户是否为审核员。"""
    user_id = user_id or session.get('user_id')
    if not user_id:
        return False
    conn = get_db()
    if not conn:
        return False
    cursor = None
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM auditor WHERE user_id = %s', (user_id,))
        return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"审核员校验失败: {e}")
        return False
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def login_required_api(view):
    """API 装饰器：未登录直接 401 JSON。"""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': '请先登录', 'message': '请先登录'}), 401
        return view(*args, **kwargs)
    return wrapper


def auditor_required_api(view):
    """API 装饰器：必须是审核员，否则 403 JSON。

    修复前 /reject_video、/tongguo_video 只校验登录（或完全不校验），
    任何注册用户都能让任意投稿过审 / 被拒。
    """
    @wraps(view)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': '请先登录', 'message': '请先登录'}), 401
        if not is_auditor():
            logger.warning(
                f"越权拦截: user_id={session.get('user_id')} "
                f"username={session.get('username')} path={request.path}"
            )
            return jsonify({'success': False, 'error': '需要审核员权限',
                            'message': '需要审核员权限'}), 403
        return view(*args, **kwargs)
    return wrapper


# ============= 降级保护：数据库不可用时数据接口返回 503 =============
_DB_STATE = {'ok': None, 'checked_at': 0.0}

# 需要数据库的路由前缀；其余（页面、静态资源、/healthz）不受影响。
# 注意：/NOAIshenhe 是页面而非接口，它自己的鉴权逻辑会把匿名用户重定向到登录页，
# 让它走 503 反而不友好，所以不列进来。
_DB_ROUTE_PREFIXES = ('/api/', '/upload', '/tongguo_video', '/reject_video',
                      '/shenheyuan')


def db_available(max_age=5.0):
    """带缓存的数据库可用性探测，避免每个请求都新开一次连接。"""
    now = time.time()
    if _DB_STATE['ok'] is None or (now - _DB_STATE['checked_at']) > max_age:
        conn = get_db()
        _DB_STATE['ok'] = conn is not None
        _DB_STATE['checked_at'] = now
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return bool(_DB_STATE['ok'])


@app.before_request
def _require_db_for_data_routes():
    """数据库不可用时，数据类接口统一返回 503 JSON。

    修复：原先 get_db() 返回 None 后，路由里的 conn.cursor() 会抛 AttributeError，
    表现为 500 + traceback（debug 下直接暴露源码）。现在明确 503 和可读提示。
    """
    if not request.path.startswith(_DB_ROUTE_PREFIXES):
        return None
    if request.url_rule is None:
        # 路径根本没匹配到路由：交给 404 处理器，别误报成"数据库不可用"
        return None
    if db_available():
        return None
    return jsonify({
        'success': False,
        'error': '数据库暂不可用：请确认 MySQL 已启动、库已创建、口令正确，'
                 '状态见 /healthz',
    }), 503


def create_users_table():
    conn = get_db()
    if not conn:
        logger.error("数据库不可用，跳过建表")
        return False
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INT PRIMARY KEY AUTO_INCREMENT,
            username VARCHAR(80) UNIQUE NOT NULL,
            password VARCHAR(200) NOT NULL,
            email VARCHAR(120),
            name VARCHAR(100) NOT NULL,
            school VARCHAR(100) DEFAULT '未公开',
            touxiang_url VARCHAR(500),
            bg_url VARCHAR(500),
            signature VARCHAR(500) DEFAULT '我是超级个性签名qwq......',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS lishi(
                   id INT PRIMARY KEY AUTO_INCREMENT,
                   user_id INT,
                   video_id INT,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (user_id) REFERENCES users(id),
                   FOREIGN KEY (video_id) REFERENCES video_records(id)
                   )''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS video_records (
            id INT PRIMARY KEY AUTO_INCREMENT,
            user_id INT,
            username VARCHAR(80),
            zuopin_name VARCHAR(255),
            saved_name VARCHAR(255),
            zuopin_path VARCHAR(500),
            zuopin_url VARCHAR(500),
            cover_path VARCHAR(500),
            file_size INT,
            upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            audit_result VARCHAR(50),
            violations VARCHAR(500),
            confidence FLOAT,
            view_count INT DEFAULT 0,
            tags VARCHAR(255),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS dianzan(
                   id INT PRIMARY KEY AUTO_INCREMENT,
                   user_id INT,
                   video_id INT,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (video_id) REFERENCES video_records(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
    )
''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS shoucang(
                   id INT PRIMARY KEY AUTO_INCREMENT,
                   user_id INT,
                   video_id INT,
                   SCname VARCHAR(45),
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (video_id) REFERENCES video_records(id),
                   FOREIGN KEY (user_id) REFERENCES users(id)
                   )''')
    # 创建新的comments表，支持回复功能
    cursor.execute('''
CREATE TABLE IF NOT EXISTS comments(
                   id INT PRIMARY KEY AUTO_INCREMENT,
                   video_id INT NOT NULL,
                   user_id INT,
                   username VARCHAR(80) NOT NULL,
                   name VARCHAR(100) NOT NULL,
                   content TEXT NOT NULL,
                   parent_id INT DEFAULT 0,
                   reply_to_user_id INT DEFAULT NULL,
                   reply_to_username VARCHAR(80) DEFAULT NULL,
                   reply_to_name VARCHAR(100) DEFAULT NULL,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (video_id) REFERENCES video_records(id),
                   FOREIGN KEY (user_id) REFERENCES users(id)
                   )''')
    #动态消息库
    cursor.execute('''
CREATE TABLE IF NOT EXISTS notifications(
                   id INT PRIMARY KEY AUTO_INCREMENT,
                   user_id INT NOT NULL,
                   type VARCHAR(20) NOT NULL,
                   sender_id INT,
                   sender_name VARCHAR(100),
                   sender_avatar VARCHAR(500),
                   video_id INT,
                   video_title VARCHAR(255),
                   comment_id INT,
                   comment_content TEXT,
                   reply_content TEXT,
                   is_read INT DEFAULT 0,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (user_id) REFERENCES users(id),
                   FOREIGN KEY (sender_id) REFERENCES users(id),
                   FOREIGN KEY (video_id) REFERENCES video_records(id)
                   )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS NOAI_shenhe(
        id INT PRIMARY KEY AUTO_INCREMENT,
        user_id INT,
        zuopin_url VARCHAR(500),
        tags VARCHAR(255),
        status VARCHAR(20) DEFAULT 'pending',
        reason VARCHAR(500) DEFAULT '审核已通过',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
    )
''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS user_dynamics(
                   id INT PRIMARY KEY AUTO_INCREMENT,
                   user_id INT NOT NULL,
                   content TEXT,
                   image_url VARCHAR(500),
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (user_id) REFERENCES users(id)
                   )''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS auditor(
        id INT PRIMARY KEY AUTO_INCREMENT,
        user_id INT,
        FOREIGN KEY (user_id) REFERENCES users(id)
                   )
''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS follows(
                   id INT PRIMARY KEY AUTO_INCREMENT,
                   follower_id INT NOT NULL,
                   followed_id INT NOT NULL,
                   created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (follower_id) REFERENCES users(id),
                   FOREIGN KEY (followed_id) REFERENCES users(id),
                   UNIQUE KEY unique_follow (follower_id, followed_id)
                   )''')
    cursor.execute('''
CREATE TABLE IF NOT EXISTS folder_backgrounds(
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    folder_name VARCHAR(100) NOT NULL,
    image_url VARCHAR(500),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    UNIQUE KEY unique_folder (user_id, folder_name)
)
''')
    # 审核员申请审批表（安全修复：不再「满 30 天自动成为审核员」，改为申请 + 管理员审批）
    cursor.execute('''
CREATE TABLE IF NOT EXISTS auditor_apply(
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY unique_apply (user_id),
    FOREIGN KEY (user_id) REFERENCES users(id)
)
''')
    # ===== 补齐历史遗留的表/字段 =====
    # 下面这两项代码里一直在用（共 19 处 SQL），但建表脚本从来没过建过 ——
    # 原项目是直接在已有数据库上手工加的，换台机器/空库就会全部 500。
    cursor.execute('''
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
          AND COLUMN_NAME = 'fav_bg_url'
    ''')
    if cursor.fetchone()[0] == 0:
        cursor.execute('ALTER TABLE users ADD COLUMN fav_bg_url VARCHAR(500) NULL')
        print("已补充字段 users.fav_bg_url")

    cursor.execute('''
CREATE TABLE IF NOT EXISTS fav_backgrounds(
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    image_url VARCHAR(500) NOT NULL,
    is_current TINYINT DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
)
''')
    conn.commit()
    cursor.close()
    conn.close()
    print("数据表创建/检查完成")

@app.route('/')
def loging():
    return render_template('login.html')



@app.route('/api/carousel', methods=['GET'])
def api_carousel():
    """获取轮播图图片"""
    carousel_type = request.args.get('type', 'poster')
    
    # 根据类型返回对应的图片列表
    carousel_images = {
        'poster': [
            '/carousel/poster1.jpg',
            '/carousel/poster2.jpg',
            '/carousel/poster3.jpg',
        ],
        'draw': [
            '/carousel/draw1.jpg',
            '/carousel/draw2.jpg',
            '/carousel/draw3.jpg',
        ],
        'craft': [
            '/carousel/craft1.jpg',
            '/carousel/craft2.jpg',
            '/carousel/craft3.jpg',
        ],
        'product': [
            '/carousel/product1.jpg',
            '/carousel/product2.jpg',
            '/carousel/product3.jpg',
        ]
    }
    
    images = carousel_images.get(carousel_type, [])
    return jsonify({'images': images})

@app.route('/carousel/<filename>')
def serve_carousel_img(filename):
    # 修复：轮播图目录改为配置项（原来写死 E 盘，本机不存在该盘）
    return send_from_directory(app.config['CAROUSEL_DIR'], filename)

@app.route('/uploadNOAI', methods=['POST'])
def uploadNOAI():
    # 1. 检查登录状态
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': '请先登录'}), 401
    
    user_id = session.get('user_id')
    
    # 2. 检查文件
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': '请选择文件'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': '文件名不能为空'}), 400
    
    # 3. 检查文件类型（加上点号）
    allowed_extensions = {'.mp4', '.mov'}
    raw_name = os.path.basename((file.filename or '').replace('\\', '/'))
    filename, ext = os.path.splitext(raw_name)
    ext = ext.lower()
    
    if ext not in allowed_extensions:
        return jsonify({'status': 'error', 'message': f'不支持的文件格式: {ext}'}), 400
    
    # 4. 生成安全文件名（清洗 + 去重，防路径穿越与重名覆盖）
    stored_name = f"{safe_filename(filename)}_{uuid.uuid4().hex[:6]}{ext}"
    
    # 5. 保存文件
    NOAI_dir = app.config['NOAI_DIR']
    os.makedirs(NOAI_dir, exist_ok=True)
    
    file_path = os.path.join(NOAI_dir, stored_name)
    if not ensure_inside(NOAI_dir, file_path):
        return jsonify({'status': 'error', 'message': '非法文件名'}), 400
    file.save(file_path)
    
    # 6. 数据库存可访问的 URL 路径
    relative_path = f"/NOAIuploads/{stored_name}"
    
    # 7. 插入数据库
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO NOAI_shenhe (user_id, zuopin_url)
            VALUES (%s, %s)
        ''', (user_id, relative_path))
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': '上传成功，等待审核',
            'file_url': relative_path
        }), 200
        
    except Exception as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        print(f'上传失败: {e}')
        return jsonify({'status': 'error', 'message': f'上传失败: {str(e)}'}), 500

@app.route('/upload', methods=['POST'])
def upload():
    if 'user_id' not in session:
        return jsonify({'error': '请先登录'}), 401
    user_id = session.get('user_id')
    username = session.get('username')
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': '请选择文件'}), 400
    file=request.files['file']
    # 安全修复：原始文件名可能含 ../ 或盘符；落盘路径一律用清洗后的名字（中文保留）
    raw_name = os.path.basename((file.filename or '').replace('\\', '/'))
    filename,ext=os.path.splitext(raw_name)
    ext=ext.lower()
    display_name = raw_name[:200] or '未命名作品'
    clean_stem = safe_filename(filename)
    stored_file = f"{clean_stem}{ext}"
    upload_dir = f"{clean_stem}_{uuid.uuid4().hex[:6]}{ext}"
    upload_root = app.config['UPLOAD_DIR']
    jpg_root = app.config['JPG_DIR']
    if ext in ['.mp4','.mov']:
        base_root = upload_root
        folder = os.path.join(upload_root, upload_dir)
    elif ext=='.jpg':
        base_root = jpg_root
        folder = os.path.join(jpg_root, upload_dir)
    else:
        return "文件类型无效！", 400
    # 双保险：解析后的落盘目录必须仍在允许的根目录内，防目录穿越
    if not ensure_inside(base_root, folder):
        return jsonify({'success': False, 'error': '非法文件名'}), 400
    temp_dir = os.path.join(upload_root, 'temp_video')
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, f'temp_{uuid.uuid4().hex[:8]}{ext}')
    file.save(temp_path)
    
    # 2. 抽帧
    frames = extract_frames(temp_path, max_frames=15)

    # 3. AI审核（安全修复：AI 模块不可用时降级为「拒绝并提示走人工通道」，不再 500）
    if video_auditor:
        result = video_auditor.audit_video(frames, display_name)
    else:
        result = {'passed': False, 'score': 0,
                  'message': 'AI 审核模块不可用，请改用人工审核通道投稿'}
    
    # 4. 如果不通过，删除临时文件并拒绝
    if not result.get('passed', False):
        os.remove(temp_path)
        return jsonify({
            'success': False,
            'error': '审核不通过',
            'score': result.get('score'),
            'message': result.get('message')
        }), 403
    os.makedirs(folder, exist_ok=True)
    filePath = os.path.join(folder, stored_file)
    shutil.move(temp_path, filePath)
    file_size=os.path.getsize(filePath)
    if ext in ['.mp4','.mov']:
        zuopin_url = media_url(f"uploads/{upload_dir}/{stored_file}")
    else:
        zuopin_url = media_url(f"static/jpg/{upload_dir}/{stored_file}")
    cover=None    
    if ext in ['.mp4','.mov']:
        cover=vitalframe(filePath,folder)

    if cover:
        cover = media_url(f"uploads/{upload_dir}/vitalframe.jpg")
    else:
        cover = None
    conn=get_db()
    cursor=conn.cursor()
    tags = result.get('tags', [])
    tags_str = ','.join(tags) if tags else ''
    sql="""
        INSERT INTO video_records
        (user_id,username,zuopin_name,saved_name,zuopin_path,zuopin_url,file_size,cover_path,upload_time,tags)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
    cursor.execute(sql,(
        user_id,
        username,
        display_name,
        stored_file,
        filePath,
        zuopin_url,
        file_size,
        cover,
        datetime.now(),
        tags_str
    ))
    conn.commit()
    zuopin_id=cursor.lastrowid
    cursor.close()
    conn.close()
    return jsonify({
            'success': True,
            'zuopin_id': zuopin_id,
            'filePath': filePath,
            'video_url': zuopin_url,
            'cover': cover,
            'file_size': file_size,
            'audit_score': result.get('score')
        })

@app.route('/tongguo_video', methods=['POST'])
@auditor_required_api
def tongguo_video():
    # 安全修复：原实现只校验「已登录」，任何注册用户都能让任意投稿过审入库。
    # 现要求必须是审核员（auditor 表内用户）。
    # 2. 获取待审核视频的 ID 和标签
    shenhe_id = request.form.get('shenhe_id')
    tag = request.form.get('tag')
    if not shenhe_id:
        return jsonify({'error': '缺少视频ID'}), 400
    # 3. 从 NOAI_shenhe 表获取投稿者信息
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute('''
        SELECT id, zuopin_url, user_id
        FROM NOAI_shenhe
        WHERE id = %s
    ''', (shenhe_id,))
    pending_video = cursor.fetchone()
    if not pending_video:
        cursor.close()
        conn.close()
        return jsonify({'error': '视频不存在'}), 404
    # 投稿者的 ID
    uploader_id = pending_video['user_id']
    zuopin_url = pending_video['zuopin_url']
    # 4. 获取投稿者用户名
    cursor.execute('SELECT username FROM users WHERE id = %s', (uploader_id,))
    user = cursor.fetchone()
    username = user['username'] if user else '未知用户'
    # 5. 从 URL 获取文件名
    filename = os.path.basename(zuopin_url)
    name_without_ext, ext = os.path.splitext(filename)
    # 6. 生成存储路径
    upload_root = app.config['UPLOAD_DIR']
    folder = os.path.join(upload_root, name_without_ext)
    os.makedirs(folder, exist_ok=True)
    # 7. 移动文件
    old_path = os.path.join(app.config['NOAI_DIR'], filename)
    new_path = os.path.join(folder, filename)
    shutil.move(old_path, new_path)
    
    # 8. 获取文件大小
    file_size = os.path.getsize(new_path)
    
    # 9. 生成 URL
    if ext.lower() in ['.mp4', '.mov']:
        zuopin_url_new = media_url(f"uploads/{name_without_ext}/{filename}")
    else:
        zuopin_url_new = media_url(f"static/jpg/{filename}")
    
    # 10. 生成封面
    cover = None
    if ext.lower() in ['.mp4', '.mov']:
        cover = vitalframe(new_path, folder)
        if cover:
            cover = media_url(f"uploads/{name_without_ext}/vitalframe.jpg")
    
    # 11. 插入 video_records 表
    try:
        sql = """
            INSERT INTO video_records
            (user_id, username, zuopin_name, saved_name, zuopin_path, zuopin_url, file_size, cover_path, upload_time, tags)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(sql, (
            uploader_id,        # 投稿者 ID
            username,           # 投稿者用户名
            filename,           # 作品名
            filename,           # 保存名
            new_path,           # 文件路径
            zuopin_url_new,     # 访问 URL
            file_size,          # 文件大小
            cover,              # 封面
            datetime.now(),     # 上传时间
            tag                 # 标签
        ))
        conn.commit()
        zuopin_id = cursor.lastrowid
        
        # 12. 删除NOAI_shenhe审核成功视频
        cursor.execute('DELETE FROM NOAI_shenhe WHERE id = %s', (shenhe_id,))
        conn.commit()
        cursor.close()
        conn.close()
       
        return jsonify({
            'success': True,
            'zuopin_id': zuopin_id,
            'filePath': new_path,
            'video_url': zuopin_url_new,
            'cover': cover,
            'file_size': file_size
        }), 200
        
    except Exception as e:
        # 回滚：把文件移回去
        if os.path.exists(new_path):
            shutil.move(new_path, old_path)
        cursor.close()
        conn.close()
        print(f'入库失败: {e}')
        return jsonify({'error': f'入库失败: {str(e)}'}), 500
    
@app.route('/reject_video', methods=['POST'])
@auditor_required_api
def reject_video():
    # 安全修复：原实现完全无鉴权，任何人 POST 即可把任意投稿标记为「已拒绝」
    shenhe_id = request.form.get('shenhe_id')
    reason = request.form.get('reason', '违规内容')
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE NOAI_shenhe 
        SET status = 'rejected', reason = %s 
        WHERE id = %s
    ''', (reason, shenhe_id))
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/confirm_reject', methods=['POST'])
def confirm_reject():
    user_id = session.get('user_id')
    shenhe_id = request.form.get('shenhe_id')
    if not user_id:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute('SELECT * FROM NOAI_shenhe WHERE id = %s AND user_id = %s AND status = "rejected"', (shenhe_id, user_id))
    record = cursor.fetchone()
    if record:
        filename = os.path.basename(record['zuopin_url'])
        file_path = os.path.join(app.config['NOAI_DIR'], filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        cursor.execute('DELETE FROM NOAI_shenhe WHERE id = %s', (shenhe_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True})
    cursor.close()
    conn.close()
    return jsonify({'success': False, 'error': '记录不存在或无权限'}), 403

@app.route('/api/user/video_status')
def get_user_video_status():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute('''
        SELECT id, zuopin_url, status, reason, tags, created_at
        FROM NOAI_shenhe
        WHERE user_id = %s
        ORDER BY created_at DESC
    ''', (user_id,))
    records = cursor.fetchall()
    cursor.close()
    conn.close()
    for item in records:
        if item.get('created_at'):
            item['created_at'] = item['created_at'].strftime('%Y-%m-%d %H:%M:%S')
    return jsonify({'success': True, 'records': records})

@app.route('/api/videosNOAI', methods=['GET'])
@auditor_required_api
def get_videosNOAI():
    # 安全修复：待审核队列是审核员工作台数据，不应向所有（含匿名）用户开放
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    # 关联 users 表查询作者姓名
    cursor.execute('''
        SELECT N.id,  N.zuopin_url, N.user_id, N.tags
        FROM NOAI_shenhe N
        LEFT JOIN users u ON N.user_id = u.id
        WHERE N.status = 'pending'
        ORDER BY N.created_at DESC
        LIMIT 1
    ''')
    zuopin = cursor.fetchall()
    cursor.close()
    conn.close()
    
    # 格式化时间
    for item in zuopin:
        if item.get('upload_time'):
            item['upload_time'] = item['upload_time'].strftime('%Y-%m-%d %H:%M:%S')
    
    return jsonify({'video': zuopin})


@app.route('/api/videos', methods=['GET'])
def get_videos():
    # 修复 1：原先 SELECT 里没有 tags，而 shipin1~5 五个页面的 JS 是按 v.tags 过滤分类的，
    #         所以这五个页面恒显示"该分类暂无视频"。这里补上 tags。
    # 修复 2：补上 username（前端两套字段名 author_name / username 都在用）。
    # 修复 3：支持可选分页 ?page=1&size=20；不传时行为与原来完全一致（全量返回）。
    page = request.args.get('page', type=int)
    size = request.args.get('size', type=int)
    paginate = bool(page and size)
    if paginate:
        page = max(1, page)
        size = max(1, min(size, 100))

    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    # 关联 users 表查询作者姓名
    sql = '''
        SELECT v.id, v.user_id, v.zuopin_name, v.zuopin_url, v.cover_path, v.file_size,
               v.upload_time, v.view_count, v.tags, v.username, u.name as author_name
        FROM video_records v
        LEFT JOIN users u ON v.user_id = u.id
        ORDER BY v.upload_time DESC
    '''
    total = None
    if paginate:
        cnt = conn.cursor()
        cnt.execute('SELECT COUNT(*) FROM video_records')
        total = cnt.fetchone()[0]
        cnt.close()
        cursor.execute(sql + ' LIMIT %s OFFSET %s', (size, (page - 1) * size))
    else:
        cursor.execute(sql)
    zuopin = cursor.fetchall()
    cursor.close()
    conn.close()
    
    # 格式化时间
    for item in zuopin:
        if item.get('upload_time'):
            item['upload_time'] = item['upload_time'].strftime('%Y-%m-%d %H:%M:%S')
    
    payload = {'video': zuopin}
    if paginate:
        payload.update({'page': page, 'size': size, 'total': total,
                        'has_more': page * size < (total or 0)})
    return jsonify(payload)

@app.route('/api/video/<int:id>',methods=['GET'])#根据id获取单个视频信息
def get_video(id):
    conn=get_db()
    cursor=conn.cursor(pymysql.cursors.DictCursor)
    # 修复：原来 SELECT * 会把 zuopin_path（服务器绝对路径）、saved_name 一起吐给前端，
    # 属于不必要的信息暴露。改为显式列白名单（前端只用这些字段）。
    cursor.execute('''
        SELECT id, user_id, username, zuopin_name, zuopin_url, cover_path,
               file_size, upload_time, view_count, tags, audit_result
        FROM video_records WHERE id=%s
    ''', (id,))
    zuopin=cursor.fetchone()
    cursor.close()
    conn.close()
    if zuopin:
        return jsonify({'zuopin':zuopin})
    return jsonify({'error':'视频不存在!'})

@app.route('/api/user/liked-videos/<int:user_id>', methods=['GET'])
@login_required_api
def get_likevideos(user_id):
    # 安全修复：原实现无鉴权也不比对 session，遍历 user_id 即可拉取任意用户点赞列表（IDOR）
    if user_id != session.get('user_id'):
        return jsonify({'success': False, 'error': '无权查看其他用户的数据'}), 403
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute('''
        SELECT d.*, v.id as video_id, v.zuopin_name, v.cover_path, v.zuopin_url, 
               v.view_count, v.username as author_name
        FROM dianzan d
        JOIN video_records v ON d.video_id = v.id
        WHERE d.user_id = %s
        ORDER BY d.created_at DESC
    ''', (user_id,))
    likevideos = cursor.fetchall()
    cursor.close()
    conn.close()
    
    if likevideos:
        return jsonify({'success': True, 'likevideo': likevideos})
    return jsonify({'success': False, 'error': '未查询到点赞视频！'})


@app.route('/api/user/history-videos/<int:user_id>', methods=['GET'])
@login_required_api
def get_lishivideos(user_id):
    # 安全修复：同上，浏览历史属于隐私数据（IDOR）
    if user_id != session.get('user_id'):
        return jsonify({'success': False, 'error': '无权查看其他用户的数据'}), 403
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute('''
        SELECT l.*, v.id as video_id, v.zuopin_name, v.cover_path, v.zuopin_url,
               v.view_count, v.username as author_name
        FROM lishi l
        JOIN video_records v ON l.video_id = v.id
        WHERE l.user_id = %s
        ORDER BY l.created_at DESC
    ''', (user_id,))
    lishivideos = cursor.fetchall()
    cursor.close()
    conn.close()
    
    if lishivideos:
        return jsonify({'success': True, 'lishivideo': lishivideos})
    return jsonify({'success': False, 'error': '未找到历史浏览作品记录'})

# ============= 浏览历史 API =============

@app.route('/api/history/add', methods=['POST'])
def add_history():
    """添加浏览历史"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    user_id = session['user_id']
    data = request.get_json()
    video_id = data.get('video_id')
    if not video_id:
        return jsonify({'success': False, 'error': '缺少视频ID'}), 400
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 先删除已存在的相同记录（避免重复）
        cursor.execute('DELETE FROM lishi WHERE user_id = %s AND video_id = %s', (user_id, video_id))
        # 插入新记录
        cursor.execute('INSERT INTO lishi (user_id, video_id) VALUES (%s, %s)', (user_id, video_id))
        # 限制每个用户最多保留50条历史
        cursor.execute('''
            DELETE FROM lishi 
            WHERE user_id = %s 
            AND id NOT IN (
                SELECT id FROM (
                    SELECT id FROM lishi WHERE user_id = %s ORDER BY created_at DESC LIMIT 50
                ) AS temp
            )
        ''', (user_id, user_id))
        conn.commit()
        return jsonify({'success': True, 'message': '记录成功'})
    except Exception as e:
        conn.rollback()
        logger.error(f"记录历史失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/history/delete', methods=['POST'])
def delete_history():
    """删除单条浏览历史"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    user_id = session['user_id']
    data = request.get_json()
    video_id = data.get('video_id')
    if not video_id:
        return jsonify({'success': False, 'error': '缺少视频ID'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM lishi WHERE user_id = %s AND video_id = %s', (user_id, video_id))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/api/history/clear', methods=['POST'])
def clear_history():
    """清空所有浏览历史"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM lishi WHERE user_id = %s', (user_id,))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

# ============= 浏览量 API =============

@app.route('/api/video/<int:video_id>/view', methods=['POST'])
def increment_video_views(video_id):
    """增加视频播放量"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 播放量 +1
        cursor.execute('UPDATE video_records SET view_count = view_count + 1 WHERE id = %s', (video_id,))
        conn.commit()
        
        # 获取最新播放量
        cursor.execute('SELECT view_count FROM video_records WHERE id = %s', (video_id,))
        result = cursor.fetchone()
        views = result[0] if result else 0
        
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'views': views})
    except Exception as e:
        logger.error(f"更新播放量失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500




@app.route('/api/user/<int:user_id>/shoucangjia/<string:SCname>',methods=['GET'])#调用收藏数据库(每个收藏夹中的每个视频)
@login_required_api
def get_SCdoc(user_id,SCname):
    # 安全修复：原实现无鉴权（IDOR），任何人可读任意用户收藏夹内容
    if user_id != session.get('user_id'):
        return jsonify({'success': False, 'error': '无权查看其他用户的收藏'}), 403
    conn=get_db()
    cursor=conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT * FROM shoucang WHERE user_id=%s AND SCname=%s",(user_id,SCname))
    SCname_video=cursor.fetchone()
    cursor.close()
    conn.close()
    if SCname_video:
        return jsonify({'SCname_video':SCname_video})
    return jsonify({'error':'未找到此收藏夹的此视频!'})


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(app.config['UPLOAD_DIR'], filename)

@app.route('/static/<path:filename>')
def serve_static(filename):
    # 修复：改用 Flask 的 static_folder（项目内 static），不再写死 E 盘路径
    return send_from_directory(app.static_folder, filename)

@app.route('/NOAIuploads/<filename>')
def serve_noai_uploads(filename):    # 安全修复：待审/被拒投稿原先可被任何人匿名下载（审核员还没看、投稿人还没确认）。
    # 现在只允许：审核员、或该投稿记录的所有者本人。
    if 'user_id' not in session:
        return jsonify({'error': '请先登录'}), 401
    if not is_auditor():
        conn_ = get_db()
        if not conn_:
            return jsonify({'error': '服务异常'}), 500
        cur_ = None
        try:
            cur_ = conn_.cursor(pymysql.cursors.DictCursor)
            cur_.execute('SELECT user_id FROM NOAI_shenhe WHERE zuopin_url LIKE %s',
                         (f'%{filename}%',))
            row = cur_.fetchone()
        finally:
            if cur_ is not None:
                try:
                    cur_.close()
                except Exception:
                    pass
            conn_.close()
        if not row or row.get('user_id') != session.get('user_id'):
            return jsonify({'error': '无权访问该文件'}), 403
    return send_from_directory(app.config['NOAI_DIR'], filename)

@app.route('/index')
def index():
    return render_template('index.html')


@app.route('/goto')
def goto_index():
    """修复：6 个个人主页模板的顶栏写的是 href="/goto"，而后端从来没有这个路由（一直 404）。"""
    return redirect(url_for('index'))

@app.route('/register', methods=['POST', 'GET'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        password1 = request.form.get('password1')
        email = request.form.get('email')
        print("收到注册请求")
        if not all([username, password, email, password1]):
            return render_template('register.html', error="请填写所有信息！")
        if password != password1:
            return render_template('register.html', error="再次输入密码有误!")
        conn = get_db()
        if conn:
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute('SELECT * FROM users WHERE username=%s OR email=%s', (username, email))
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            if user:
                return render_template('register.html', error="用户名已存在!或邮箱已注册!")
        password_hash = generate_password_hash(password)
        new_user = User(
            username=username,
            email=email,
            password=password_hash,
            name=username
        )
        try:
            db.session.add(new_user)
            db.session.commit()
            flash("注册成功!")
            print(f"用户{username}已存入数据库,ID:{new_user.id}")
        except Exception as e:
            db.session.rollback()
            return "数据库错误"
    return render_template('register.html')

@app.route('/login', methods=['POST', 'GET'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = None
        conn = get_db()
        if conn:
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute('SELECT * FROM users WHERE username=%s', (username,))
            user = cursor.fetchone()
            cursor.close()
            conn.close()
            if user and check_password_hash(user['password'], password):
                session.clear()      # 安全修复：登录后重建会话，防会话固定（Session Fixation）
                session['user_id'] = user['id']
                session['username'] = user['username']
                session['name'] = user.get('name', user['username'])
                return redirect(url_for('index'))
            else:
                return render_template('login.html', error="用户名或密码错误!")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

#修改学校api===
@app.route('/api/changeSchool', methods=['POST'])
def change_school():
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录'}), 401
    
    data = request.get_json()
    school = data.get('school', '').strip()
    if not school:
        return jsonify({'msg': '学校不能为空'}), 400
    
    user_id = session['user_id']
    conn = get_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET school = %s WHERE id = %s', (school, user_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'msg': '修改成功'})
    return jsonify({'msg': '数据库错误'}), 500

# ============= 用户 API（前后端分离） =============
@app.route('/api/getuserinfor')
def get_user():
    if 'user_id' not in session:
        return jsonify({"name": "未登录"}), 401
    user_id = session['user_id']
    conn=get_db()
    if conn:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute('SELECT id,username,name,school,touxiang_url,bg_url FROM users WHERE id=%s', (user_id,))
        user = cursor.fetchone()
        # 获取总获赞数
        cursor.execute('''
            SELECT COUNT(*) as total_likes 
            FROM dianzan d 
            JOIN video_records v ON d.video_id = v.id 
            WHERE v.user_id = %s
        ''', (user_id,))
        result = cursor.fetchone()
        user['total_likes'] = result['total_likes'] if result else 0
        cursor.close() 
        conn.close()
        return jsonify(user)
    return jsonify({"name": "获取失败"})
#设置功能的api
#用户头像存储功能
AVATAR_FOLDER = app.config['AVATAR_DIR']
os.makedirs(AVATAR_FOLDER, exist_ok=True)

@app.route('/api/upload_avatar', methods=['POST'])
def upload_avatar():
    """上传头像图片"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    
    if 'avatar' not in request.files:
        return jsonify({'success': False, 'error': '请选择图片'}), 400
    
    file = request.files['avatar']
    if file.filename == '':
        return jsonify({'success': False, 'error': '文件名不能为空'}), 400
    # 检查文件类型
    allowed_ext = {'.jpg', '.jpeg', '.png', '.gif'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        return jsonify({'success': False, 'error': '只支持 jpg、png、gif 格式'}), 400
    # 生成唯一文件名
    filename = f'user_{user_id}_{uuid.uuid4().hex[:8]}{ext}'
    filepath = os.path.join(AVATAR_FOLDER, filename)
    file.save(filepath)
    # 生成访问URL
    avatar_url = f'/static/avatars/{filename}'
    
    return jsonify({
        'success': True,
        'avatar_url': avatar_url
    })

@app.route('/api/changeTouxiang',methods=['POST'])
def update_touxiang():
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录'}), 401
    data=request.get_json()
    new_url=data.get('new_touxiang')
    if not new_url:
        return jsonify({'msg': '未上传新头像'}), 400
    user_id = session['user_id']
    conn=get_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET touxiang_url = %s WHERE id = %s', (new_url, user_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'msg':'修改成功'})

# 背景修改功能
BG_FOLDER = app.config['BG_DIR']
os.makedirs(BG_FOLDER, exist_ok=True)

@app.route('/api/upload_bg', methods=['POST'])
def upload_bg():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    file = request.files.get('bg')
    if not file or file.filename == '':
        return jsonify({'success': False, 'error': '请选择图片'}), 400
    
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png']:
        return jsonify({'success': False, 'error': '只支持 jpg、png'}), 400
    
    filename = f'bg_{session["user_id"]}_{uuid.uuid4().hex[:8]}{ext}'
    filepath = os.path.join(BG_FOLDER, filename)
    file.save(filepath)
    
    return jsonify({'success': True, 'bg_url': f'/static/backgrounds/{filename}'})
#更新签名
@app.route('/api/signature',methods=['POST'])
def update_signature():
    if 'user_id' not in session:
        return jsonify({'msg':'请先登录'}),401
    data =request.get_json()
    new_signature=data.get('new_signature')
    user_id=session['user_id']
    conn=get_db()
    if conn:
        cursor=conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute('SELECT signature FROM users WHERE id= %s', (user_id,))
        user=cursor.fetchone()
        old_signature=user['signature'] if user else None
        cursor.close()
    if new_signature==old_signature:
        return jsonify({'msg': '签名未改变', 'new_signature': old_signature})
    else:
        cursor2=conn.cursor(pymysql.cursors.DictCursor)
        cursor2.execute('UPDATE users SET signature =%s WHERE id=%s',(new_signature,user_id))
        conn.commit()
        cursor2.close()
        conn.close()
        return jsonify({'new_signature':new_signature})
#获取签名
@app.route('/api/get-signature', methods=['GET'])
def get_signature():
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录'}), 401
    
    user_id = session['user_id']
    conn = get_db()
    if not conn:
        return jsonify({'msg': '数据库连接失败'}), 500
    
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute('SELECT signature FROM users WHERE id = %s', (user_id,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    return jsonify({'signature': user['signature'] if user else ''})

BG_FOLDER1 = app.config['FAV_BG_DIR']
os.makedirs(BG_FOLDER1, exist_ok=True)


# ============= 收藏夹单独背景管理 =============

@app.route('/api/setFolderBg', methods=['POST'])
def set_folder_bg():
    """设置指定收藏夹的背景图"""
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录'}), 401
    
    data = request.get_json()
    folder_name = data.get('folder_name')
    image_url = data.get('image_url')
    
    if not folder_name:
        return jsonify({'msg': '缺少收藏夹名称'}), 400
    
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    
    # 如果 image_url 为空，删除记录（恢复默认）
    if not image_url:
        cursor.execute('DELETE FROM folder_backgrounds WHERE user_id = %s AND folder_name = %s', 
                      (user_id, folder_name))
    else:
        cursor.execute('''
            INSERT INTO folder_backgrounds (user_id, folder_name, image_url)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE image_url = VALUES(image_url)
        ''', (user_id, folder_name, image_url))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'msg': '设置成功'})


@app.route('/api/getFolderBg/<folder_name>', methods=['GET'])
def get_folder_bg(folder_name):
    """获取指定收藏夹的背景图"""
    if 'user_id' not in session:
        return jsonify({'bg_url': ''})
    
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT image_url FROM folder_backgrounds WHERE user_id = %s AND folder_name = %s', 
                  (user_id, folder_name))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    
    return jsonify({'bg_url': result[0] if result else ''})
    
#获取现存收藏夹背景指定列表
@app.route('/api/listBgImages')
def list_bg_images():
    if 'user_id' not in session:
        return jsonify({"list": []})
    
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    
    cursor.execute('''
        SELECT id, image_url, is_current, created_at
        FROM fav_backgrounds 
        WHERE user_id = %s 
        ORDER BY created_at DESC
    ''', (user_id,))
    
    images = cursor.fetchall()
    cursor.close()
    conn.close()
    
    img_list = []
    for img in images:
        filename = os.path.basename(img['image_url'])
        img_list.append({
            "id": img['id'],
            "name": filename,
            "url": img['image_url'],
            "is_current": img['is_current']
        })
    
    return jsonify({"list": img_list})

#删除指定图片
@app.route('/api/delBgImage', methods=['POST'])
def del_bg_image():
    if 'user_id' not in session:
        return jsonify({"msg": "请先登录"}), 401
    
    user_id = session['user_id']
    data = request.get_json()
    image_id = data.get('image_id')
    filename = data.get('name', '')
    
    if not image_id and not filename:
        return jsonify({"msg": "参数错误"})
    
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    
    # 查找要删除的图片
    if image_id:
        cursor.execute('SELECT * FROM fav_backgrounds WHERE id = %s AND user_id = %s', 
                      (image_id, user_id))
    else:
        cursor.execute('SELECT * FROM fav_backgrounds WHERE image_url LIKE %s AND user_id = %s', 
                      (f'%{filename}%', user_id))
    
    image = cursor.fetchone()
    if not image:
        cursor.close()
        conn.close()
        return jsonify({"msg": "图片不存在或无权限"})
    
    # 删除物理文件
    filepath = os.path.join(BG_FOLDER1, os.path.basename(image['image_url']))
    if os.path.exists(filepath):
        os.remove(filepath)
    
    # 删除数据库记录
    cursor.execute('DELETE FROM fav_backgrounds WHERE id = %s', (image['id'],))
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({"msg": "删除成功"})
@app.route('/api/cleanAllInvalidFavBg', methods=['POST'])
@auditor_required_api
def clean_all_invalid_fav_bg():
    """一次性清理所有无效的收藏夹背景记录（新旧表都清理）

    安全修复：原实现完全无鉴权，任何人访问一次就会 os.remove 背景图片并删库记录。
    现要求审核员权限，并改为 POST（会删文件的接口不该是 GET）。
    """
    import os
    
    cleaned_users = 0
    cleaned_backgrounds = 0
    
    # ===== 1. 清理旧表 users.fav_bg_url =====
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT id, fav_bg_url FROM users WHERE fav_bg_url IS NOT NULL AND fav_bg_url != ''")
    users = cursor.fetchall()
    
    for user in users:
        bg_filename = os.path.basename(user['fav_bg_url'])
        file_path = os.path.join(BG_FOLDER1, bg_filename)
        
        if not os.path.exists(file_path):
            update_cursor = conn.cursor()
            update_cursor.execute("UPDATE users SET fav_bg_url = NULL WHERE id=%s", [user['id']])
            conn.commit()
            update_cursor.close()
            cleaned_users += 1
            print(f"清理旧表无效路径: {user['fav_bg_url']}")
    
    cursor.close()
    conn.close()
    
    # ===== 2. 清理新表 fav_backgrounds =====
    conn2 = get_db()
    cursor2 = conn2.cursor(pymysql.cursors.DictCursor)
    cursor2.execute("SELECT id, image_url FROM fav_backgrounds")
    backgrounds = cursor2.fetchall()
    
    for bg in backgrounds:
        bg_filename = os.path.basename(bg['image_url'])
        file_path = os.path.join(BG_FOLDER1, bg_filename)
        
        if not os.path.exists(file_path):
            del_cursor = conn2.cursor()
            del_cursor.execute("DELETE FROM fav_backgrounds WHERE id=%s", [bg['id']])
            conn2.commit()
            del_cursor.close()
            cleaned_backgrounds += 1
            print(f"清理新表无效路径: {bg['image_url']}")
    
    cursor2.close()
    conn2.close()
    
    # ===== 3. 清理物理文件夹中的孤立文件（可选）=====
    orphan_files = []
    if os.path.exists(BG_FOLDER1):
        # 获取数据库中所有有效URL
        conn3 = get_db()
        cursor3 = conn3.cursor()
        cursor3.execute("SELECT image_url FROM fav_backgrounds")
        db_urls = {row[0] for row in cursor3.fetchall() if row[0]}
        cursor3.execute("SELECT fav_bg_url FROM users WHERE fav_bg_url IS NOT NULL AND fav_bg_url != ''")
        db_urls.update(row[0] for row in cursor3.fetchall() if row[0])
        cursor3.close()
        conn3.close()
        
        # 检查文件夹中的文件
        for filename in os.listdir(BG_FOLDER1):
            file_url = f'/static/dynamics/bg/{filename}'
            if file_url not in db_urls:
                file_path = os.path.join(BG_FOLDER1, filename)
                try:
                    os.remove(file_path)
                    orphan_files.append(filename)
                    print(f"删除孤立文件: {filename}")
                except Exception as e:
                    print(f"删除孤立文件失败: {filename} - {e}")
    
    return jsonify({
        'success': True,
        'msg': f'清理完成！旧表清理 {cleaned_users} 条，新表清理 {cleaned_backgrounds} 条，孤立文件 {len(orphan_files)} 个',
        'details': {
            'cleaned_users': cleaned_users,
            'cleaned_backgrounds': cleaned_backgrounds,
            'orphan_files': orphan_files
        }
    })

#获取当前收藏背景
@app.route('/api/getUserFavBg')
def get_user_fav_bg():
    if 'user_id' not in session:
        return jsonify({'fav_bg': ''})
    
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    
    # 先从新表获取
    cursor.execute('''
        SELECT image_url FROM fav_backgrounds 
        WHERE user_id = %s AND is_current = 1
    ''', (user_id,))
    
    current = cursor.fetchone()
    
    if current:
        bg_url = current['image_url']
        filename = os.path.basename(bg_url)
        filepath = os.path.join(BG_FOLDER1, filename)
        if os.path.exists(filepath):
            cursor.close()
            conn.close()
            return jsonify({'fav_bg': bg_url})
        else:
        # 文件不存在，清理新表记录
            print(f"新表文件不存在，清理: {bg_url}")
            cursor.execute("DELETE FROM fav_backgrounds WHERE user_id = %s AND is_current = 1", [user_id])
            conn.commit()
    
    # 如果新表没有，回退到旧表查询
    cursor.execute("SELECT fav_bg_url FROM users WHERE id=%s", (user_id,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    fav_bg = ''
    if user and user['fav_bg_url']:
        bg_filename = os.path.basename(user['fav_bg_url'])
        file_path = os.path.abspath(os.path.join(BG_FOLDER1, bg_filename))
    
        if os.path.exists(file_path):
            fav_bg = user['fav_bg_url']
        else:
            # 文件不存在，清空数据库记录
            conn2 = get_db()
            if conn2:
                cursor2 = conn2.cursor()
                cursor2.execute("UPDATE users SET fav_bg_url = NULL WHERE id=%s", (user_id,))
                conn2.commit()
                cursor2.close()
                conn2.close()
    
    return jsonify({'fav_bg': fav_bg})


@app.route('/api/uploadSCbg', methods=['POST'])
def upload_SCbg():
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录', 'success': False}), 401

    if 'bg_file' not in request.files:
        return jsonify({'msg': '未选择文件', 'success': False}), 400

    file = request.files['bg_file']
    if file.filename == '':
        return jsonify({'msg': '未选择文件', 'success': False}), 400

    # 生成唯一文件名，防止覆盖（安全修复：原 rsplit 在无扩展名时会 IndexError）
    ext = os.path.splitext(os.path.basename((file.filename or '').replace('\\', '/')))[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.webp', '.gif']:
        return jsonify({'msg': '只支持图片格式', 'success': False}), 400
    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(BG_FOLDER1, filename)
    file.save(save_path)

    # 返回可访问的URL
    file_url = f'/static/dynamics/bg/{filename}'
    
    # 保存到新表
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO fav_backgrounds (user_id, image_url, is_current)
        VALUES (%s, %s, 0)
    ''', (user_id, file_url))
    conn.commit()
    image_id = cursor.lastrowid  # 获取新插入的ID
    cursor.close()
    conn.close()
    
    return jsonify({
        'success': True,
        'file_url': file_url,
        'msg': '上传成功',
        'image_id': image_id
    })

@app.route('/api/changeSCbg', methods=['POST'])
def update_SCbg():
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录'}), 401
    
    data = request.get_json()
    new_bg = data.get('new_bg')
    image_id = data.get('image_id')  # 获取图片ID
    
    if not new_bg:
        return jsonify({'msg': '未选择背景'}), 400
    
    user_id = session['user_id']
    conn = get_db()
    
    if conn:
        cursor = conn.cursor()
        
        # 取消当前用户所有图片的"当前使用"状态
        cursor.execute('UPDATE fav_backgrounds SET is_current = 0 WHERE user_id = %s', (user_id,))
        
        # 设置新的当前背景
        if image_id:
            cursor.execute('UPDATE fav_backgrounds SET is_current = 1 WHERE id = %s AND user_id = %s', 
                         (image_id, user_id))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        # 同时更新users表（保持兼容性，防止旧代码读取不到背景）
        conn2 = get_db()
        cursor2 = conn2.cursor()
        cursor2.execute('UPDATE users SET fav_bg_url = %s WHERE id = %s', (new_bg, user_id))
        conn2.commit()
        cursor2.close()
        conn2.close()
        
        return jsonify({'msg': '修改成功'})
    return jsonify({'msg': '数据库错误'}), 500


@app.route('/api/changeBg', methods=['POST'])
def update_bg():
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录'}), 401
    data = request.get_json()
    new_bg = data.get('new_bg')
    if not new_bg:
        return jsonify({'msg': '未上传新背景'}), 400
    user_id = session['user_id']
    conn = get_db()
    if conn:
        # 1. 先查询旧的背景路径
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute('SELECT bg_url FROM users WHERE id = %s', (user_id,))
        user = cursor.fetchone()
        old_bg = user['bg_url'] if user else None
        cursor.close()
        
        # 2. 更新数据库为新背景
        cursor2 = conn.cursor()
        cursor2.execute('UPDATE users SET bg_url = %s WHERE id = %s', (new_bg, user_id))
        conn.commit()
        cursor2.close()
        conn.close()
        
        # 3. 删除旧的背景文件
        if old_bg and old_bg != new_bg:
            old_filename = os.path.basename(old_bg)
            old_filepath = os.path.join(BG_FOLDER, old_filename)
            if os.path.exists(old_filepath):
                try:
                    os.remove(old_filepath)
                    print(f"✅ 已删除旧背景: {old_filename}")
                except Exception as e:
                    print(f"⚠️ 删除旧背景失败: {e}")
        
        return jsonify({'msg': '修改成功'})
    return jsonify({'msg': '数据库错误'}), 500

# 静态文件访问
@app.route('/static/backgrounds/<filename>')
def serve_bg(filename):
    return send_from_directory(BG_FOLDER, filename)
#用户改名功能
@app.route('/api/changename',methods=['POST'])
def update_username():
    if 'user_id' not in session:
        return jsonify({'msg': '请先登录'}), 401
    data=request.get_json()
    new_name=data.get('name')
    if not new_name:
        return jsonify({'msg': '名字不能为空'}), 400
    user_id = session['user_id']
    conn=get_db()
    if conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET name = %s WHERE id = %s', (new_name, user_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'msg':'修改成功'})#新注册的账号先默认是名字name为用户名username

# ============= AI审核页面路由 =============
@app.route('/video_audit')
def video_audit():
    """AI视频审核页面"""
    if 'user_id' not in session:
        flash('请先登录')
        return redirect(url_for('login'))
    return render_template('video_audit.html', username=session.get('username'))

@app.route('/api/audit', methods=['POST'])
@login_required_api
def api_audit():
    """AI视频审核API（安全修复：原先无登录校验，且 AI 缺失时 fail-open）"""
    try:
        # 检查文件
        if 'video' not in request.files:
            return jsonify({'error': '未找到视频文件'}), 400
        
        file = request.files['video']
        if file.filename == '':
            return jsonify({'error': '未选择文件'}), 400
        original_filename = request.form.get('filename', file.filename)
        
        # 保存视频
        filepath, filename = save_uploaded_video(file, app.config['UPLOAD_FOLDER'])
        logger.info(f"收到视频: {filename}")
        
        # 抽帧
        frames = extract_frames(filepath, max_frames=15)
        logger.info(f"抽帧完成，共{len(frames)}帧")
        
        # AI审核
        # 安全修复：原先 AI 模块缺失时构造 {'passed': True, 'message': '模拟模式：审核通过'}，
        # 即 fail-open，与 /upload 的 fail-closed 自相矛盾。现统一为不可用即 503。
        if not video_auditor:
            delete_video(filepath)
            return jsonify({'error': 'AI 审核模块不可用，请改用人工审核通道'}), 503
        result = video_auditor.audit_video(frames, original_filename)
        
        # 审核记录
        # 安全修复：原先把「审核记录」写进 video_records（作品表），而该表会出现在公共
        # /api/videos 列表里 —— 任何登录用户都能借此往公共作品流注入空壳卡片。
        # 审核日志改为只写应用日志（该接口对应的页面 video_audit.html 本来就不存在）。
        logger.info(
            f"审核记录 user={session.get('user_id')} file={filename} "
            f"passed={bool(result.get('passed'))} "
            f"violations={','.join(result.get('violations') or [])} "
            f"confidence={result.get('confidence', 0)}"
        )
        
        # 清理临时文件
        delete_video(filepath)
        
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"审核失败: {e}")
        return jsonify({'error': str(e)}), 500

# ============= 留言板 API =============

# ============= 评论系统（支持回复） =============

@app.route('/api/comments/<int:video_id>', methods=['GET'])
def get_comments(video_id):
    """获取指定视频的评论列表（树形结构）"""
    try:
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        # 获取所有评论（包含回复）
        cursor.execute('''
            SELECT 
                c.id,
                c.video_id,
                c.user_id,
                c.username,
                c.name,
                c.content,
                c.parent_id,
                c.reply_to_user_id,
                c.reply_to_username,
                c.reply_to_name,
                c.created_at,
                u.touxiang_url as avatar
            FROM comments c
            LEFT JOIN users u ON c.user_id = u.id 
            WHERE c.video_id = %s
            ORDER BY 
                CASE WHEN c.parent_id = 0 THEN c.id ELSE c.parent_id END DESC,
                c.created_at ASC
        ''', (video_id,))
        
        comments = cursor.fetchall()
        cursor.close()
        conn.close()
        
        # 格式化时间
        for comment in comments:
            if comment.get('created_at'):
                comment['created_at'] = comment['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        # 构建树形结构
        comment_map = {}
        root_comments = []
        
        for comment in comments:
            comment['replies'] = []
            comment_map[comment['id']] = comment
            
        for comment in comments:
            if comment['parent_id'] == 0:
                root_comments.append(comment)
            elif comment['parent_id'] in comment_map:
                comment_map[comment['parent_id']]['replies'].append(comment)
        
        return jsonify({'success': True, 'comments': root_comments})
        
    except Exception as e:
        logger.error(f"获取评论失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/comments', methods=['POST'])
def add_comment():
    """添加评论或回复"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        content = data.get('content', '').strip()
        parent_id = data.get('parent_id', 0)
        reply_to_user_id = data.get('reply_to_user_id')
        reply_to_username = data.get('reply_to_username')
        reply_to_name = data.get('reply_to_name')
        
        if not content:
            return jsonify({'success': False, 'error': '评论内容不能为空'}), 400
        
        if len(content) > 600:
            return jsonify({'success': False, 'error': '评论内容最多600字'}), 400
        
        # 获取用户信息
        user_id = session.get('user_id')
        username = session.get('username', '匿名用户')
        name = session.get('name', username)
        
        if not user_id:
            return jsonify({'success': False, 'error': '请先登录'}), 401
        
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 安全修复：reply_to_* 原先完全由客户端提供，可伪造「回复@某人」的通知推给任意用户；
        # 现改为服务端按 parent_id 反查，并校验父评论与视频的归属（防跨视频串楼）。
        try:
            video_id_int = int(video_id)
            parent_id_int = int(parent_id or 0)
        except (TypeError, ValueError):
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': '参数不合法'}), 400

        cursor.execute('SELECT id FROM video_records WHERE id = %s', (video_id_int,))
        if not cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': '视频不存在'}), 404

        if parent_id_int:
            cursor.execute(
                'SELECT id, video_id, user_id, username, name FROM comments WHERE id = %s',
                (parent_id_int,))
            parent = cursor.fetchone()
            if not parent:
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'error': '被回复的评论不存在'}), 404
            if int(parent['video_id']) != video_id_int:
                cursor.close()
                conn.close()
                return jsonify({'success': False, 'error': '评论与视频不匹配'}), 400
            reply_to_user_id = parent['user_id']
            reply_to_username = parent['username']
            reply_to_name = parent['name']

        video_id = video_id_int
        parent_id = parent_id_int

        cursor.execute('''
            INSERT INTO comments 
            (video_id, user_id, username, name, content, parent_id, 
             reply_to_user_id, reply_to_username, reply_to_name)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''', (video_id, user_id, username, name, content, parent_id,
              reply_to_user_id, reply_to_username, reply_to_name))
        
        conn.commit()
        comment_id = cursor.lastrowid
         # ========== 创建通知 ==========
# 获取视频信息
        cursor.execute('''
    SELECT user_id, username, zuopin_name FROM video_records WHERE id = %s
''', (video_id,))
        video_info = cursor.fetchone()
        if video_info:
           video_owner_id = video_info['user_id']
           video_title = video_info['zuopin_name']
    
    # 获取发送者头像
        cursor.execute('SELECT touxiang_url FROM users WHERE id = %s', (user_id,))
        sender_info = cursor.fetchone()
        sender_avatar = sender_info['touxiang_url'] if sender_info else None
    
        if parent_id == 0:
        # 顶级评论：通知视频作者
           if video_owner_id and video_owner_id != user_id:
              cursor.execute('''
                INSERT INTO notifications 
                (user_id, type, sender_id, sender_name, sender_avatar, 
                 video_id, video_title, comment_id, comment_content)
                VALUES (%s, 'comment', %s, %s, %s, %s, %s, %s, %s)
            ''', (video_owner_id, user_id, name, sender_avatar, 
                  video_id, video_title, comment_id, content))
        else:
        # 回复评论：通知被回复的用户
           if reply_to_user_id and reply_to_user_id != user_id:
            # 获取原评论内容
              cursor.execute('SELECT content FROM comments WHERE id = %s', (parent_id,))
              parent_info = cursor.fetchone()
              parent_content = parent_info['content'] if parent_info else ''
            
              cursor.execute('''
                INSERT INTO notifications 
                (user_id, type, sender_id, sender_name, sender_avatar,
                 video_id, video_title, comment_id, comment_content, reply_content)
                VALUES (%s, 'reply', %s, %s, %s, %s, %s, %s, %s, %s)
            ''', (reply_to_user_id, user_id, name, sender_avatar,
                  video_id, video_title, parent_id, parent_content, content))

        conn.commit()
# ========== 通知创建结束 ==========
        cursor.close()
        # 获取新创建的评论信息
        cursor=conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute('''
            SELECT c.*, u.touxiang_url as avatar
            FROM comments c
            LEFT JOIN users u ON c.user_id = u.id
            WHERE c.id = %s
        ''', (comment_id,))
        
        new_comment = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if new_comment and new_comment.get('created_at'):
            new_comment['created_at'] = new_comment['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'success': True,
            'comment': new_comment
        })
        
    except Exception as e:
        logger.error(f"添加评论失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
def delete_comment(comment_id):
    """删除评论（只能删除自己的，删除父评论会级联删除所有回复）"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': '请先登录'}), 401
            
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        # 检查是否有权限删除
        cursor.execute('SELECT user_id FROM comments WHERE id = %s', (comment_id,))
        result = cursor.fetchone()
        if not result:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': '评论不存在'}), 404
        
        # 允许删除的条件：是自己的评论
        if result['user_id'] != user_id:
            cursor.close()
            conn.close()
            return jsonify({'success': False, 'error': '无权删除此评论'}), 403
        
        # 删除评论（外键约束会级联删除所有回复）
        cursor.execute('DELETE FROM comments WHERE id = %s', (comment_id,))
        conn.commit()
        
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'message': '删除成功'})
        
    except Exception as e:
        logger.error(f"删除评论失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

#动态通知API========
# ============= 通知 API =============

@app.route('/api/notifications', methods=['GET'])
def get_notifications():
    """获取当前用户的通知列表"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    
    try:
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        
        cursor.execute('''
            SELECT * FROM notifications 
            WHERE user_id = %s 
            ORDER BY created_at DESC 
            LIMIT 50
        ''', (user_id,))
        
        notifications = cursor.fetchall()
        
        for n in notifications:
            if n.get('created_at'):
                n['created_at'] = n['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'notifications': notifications})
        
    except Exception as e:
        logger.error(f"获取通知失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/notifications/unread/count', methods=['GET'])
def get_unread_count():
    """获取未读通知数量"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'count': 0})
    
    user_id = session['user_id']
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT COUNT(*) FROM notifications 
            WHERE user_id = %s AND is_read = 0
        ''', (user_id,))
        
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'count': count})
        
    except Exception as e:
        logger.error(f"获取未读数量失败: {e}")
        return jsonify({'success': False, 'count': 0})


@app.route('/api/notifications/<int:notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    """标记单个通知为已读"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE notifications SET is_read = 1 
            WHERE id = %s AND user_id = %s
        ''', (notification_id, user_id))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"标记已读失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/notifications/read/all', methods=['POST'])
def mark_all_read():
    """标记所有通知为已读"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute('''
            UPDATE notifications SET is_read = 1 
            WHERE user_id = %s
        ''', (user_id,))
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True})
        
    except Exception as e:
        logger.error(f"全部已读失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============= 点赞 API =============
@app.route('/api/dianzan/status/<int:video_id>', methods=['GET'])
def get_dianzan_status(video_id):
    """检查当前用户是否已点赞该视频"""
    if 'user_id' not in session:
        return jsonify({'success': True, 'liked': False})
    
    user_id = session['user_id']
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id FROM dianzan WHERE user_id = %s AND video_id = %s',
            (user_id, video_id)
        )
        liked = cursor.fetchone() is not None
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'liked': liked})
        
    except Exception as e:
        logger.error(f"获取点赞状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/dianzan/count/<int:video_id>', methods=['GET'])
def get_dianzan_count(video_id):
    """获取视频点赞数"""
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM dianzan WHERE video_id = %s', (video_id,))
        count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'count': count})
    except Exception as e:
        logger.error(f"获取点赞数失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/dianzan/<int:video_id>', methods=['POST'])
def toggle_dianzan(video_id):
    """点赞/取消点赞"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    user_id = session['user_id']
    try:
        conn = get_db()
        cursor = conn.cursor()  
        # 检查是否已点赞
        cursor.execute('SELECT id FROM dianzan WHERE user_id = %s AND video_id = %s', (user_id, video_id))
        existing = cursor.fetchone()
        if existing:
            # 已点赞，取消点赞
            cursor.execute('DELETE FROM dianzan WHERE user_id = %s AND video_id = %s', (user_id, video_id))
            action = 'unliked'
        else:
            # 未点赞，添加点赞
            cursor.execute('INSERT INTO dianzan (user_id, video_id) VALUES (%s, %s)', (user_id, video_id))
            action = 'liked'
        
        conn.commit()
        
        # 获取最新点赞数
        cursor.execute('SELECT COUNT(*) FROM dianzan WHERE video_id = %s', (video_id,))
        count = cursor.fetchone()[0]
        
        cursor.close()
        conn.close()
        
        return jsonify({
            'success': True,
            'action': action,
            'count': count
        })
        
    except Exception as e:
        logger.error(f"点赞操作失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

#====发布动态api
# 动态图片存储目录
DYNAMIC_FOLDER = app.config['DYNAMIC_DIR']
os.makedirs(DYNAMIC_FOLDER, exist_ok=True)

@app.route('/api/dynamics', methods=['POST'])
def publish_dynamic():
    """发布动态"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    content = request.form.get('content', '').strip()
    image = request.files.get('image')
    
    if not content and not image:
        return jsonify({'success': False, 'error': '内容或图片不能都为空'}), 400

    # 安全修复：正文原先无长度上限（MAX_CONTENT_LENGTH 是 500MB），单请求可写入数百 MB
    if len(content) > 2000:
        return jsonify({'success': False, 'error': '动态正文最多 2000 字'}), 400
    
    image_url = None
    if image:
        ext = os.path.splitext(image.filename)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.gif']:
            return jsonify({'success': False, 'error': '只支持 jpg、png、gif 格式'}), 400
        
        filename = f'dynamic_{user_id}_{uuid.uuid4().hex[:8]}{ext}'
        filepath = os.path.join(DYNAMIC_FOLDER, filename)
        image.save(filepath)
        image_url = f'/static/dynamics/{filename}'
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_dynamics (user_id, content, image_url)
        VALUES (%s, %s, %s)
    ''', (user_id, content, image_url))
    conn.commit()
    
    dynamic_id = cursor.lastrowid
    cursor.close()
    conn.close()
    
    return jsonify({
        'success': True, 
        'message': '发布成功',
        'dynamic_id': dynamic_id
    })


@app.route('/api/dynamics', methods=['GET'])
def get_dynamics():
    """获取动态列表（自己和关注的用户）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    
    try:
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        
        # 获取自己和关注的用户的动态
        cursor.execute('''
            SELECT d.*, u.name as author_name, u.touxiang_url as author_avatar
            FROM user_dynamics d
            LEFT JOIN users u ON d.user_id = u.id
            WHERE d.user_id = %s 
               OR d.user_id IN (SELECT followed_id FROM follows WHERE follower_id = %s)
            ORDER BY d.created_at DESC
            LIMIT 50
        ''', (user_id, user_id))
        
        dynamics = cursor.fetchall()
        
        for d in dynamics:
            if d.get('created_at'):
                d['created_at'] = d['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'dynamics': dynamics})
        
    except Exception as e:
        logger.error(f"获取动态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    
# 静态文件访问
@app.route('/static/dynamics/<filename>')
def serve_dynamic_image(filename):
    return send_from_directory(DYNAMIC_FOLDER, filename)

# ============= 收藏 API =============

@app.route('/api/shoucang/<int:video_id>', methods=['POST'])
def toggle_shoucang(video_id):
    """收藏/取消收藏"""     
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    user_id = session['user_id']
    try:
        conn = get_db()
        cursor = conn.cursor()
        # 使用默认收藏夹
        data = request.get_json() or {}
        sc_name = data.get('sc_name', '默认收藏夹').strip()
    # 如果传了空字符串，也用默认收藏夹
        if not sc_name:
           sc_name = '默认收藏夹'
        # 检查是否已收藏
        cursor.execute(
            'SELECT id FROM shoucang WHERE user_id = %s AND video_id = %s AND SCname = %s',
            (user_id, video_id, sc_name)
        )
        existing = cursor.fetchone()
        if existing:
            # 已收藏，取消收藏
            cursor.execute(
                'DELETE FROM shoucang WHERE user_id = %s AND video_id = %s AND SCname = %s',
                (user_id, video_id, sc_name)
            )
            action = 'uncollected'
            message = '已取消收藏'
        else:
            # 未收藏，添加收藏
            cursor.execute(
                'INSERT INTO shoucang (user_id, video_id, SCname) VALUES (%s, %s, %s)',
                (user_id, video_id, sc_name)
            )
            action = 'collected'
            message = '收藏成功'
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({
            'success': True,
            'action': action,
            'message': message
        })
    except Exception as e:
        logger.error(f"收藏操作失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shoucang/folder/<sc_name>/videos', methods=['GET'])
def get_folder_videos(sc_name):
    """获取指定收藏夹内的所有视频"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    try:
        conn = get_db()
        cursor = conn.cursor()
        
        # 先获取该收藏夹下的所有 video_id
        cursor.execute(
            '''SELECT video_id, created_at 
               FROM shoucang 
               WHERE user_id = %s AND SCname = %s 
               ORDER BY created_at DESC''',
            (user_id, sc_name)
        )
        shoucang_records = cursor.fetchall()
        
        if not shoucang_records:
            cursor.close()
            conn.close()
            return jsonify({'success': True, 'videos': []})
        
        # 提取所有 video_id
        video_ids = [record[0] for record in shoucang_records]
        created_at_map = {record[0]: record[1] for record in shoucang_records}
        
        # 去 video_records 表查询视频详情
        placeholders = ','.join(['%s'] * len(video_ids))
        cursor.execute(
            f'''SELECT v.id, v.zuopin_name, v.cover_path, v.view_count,
                       (SELECT COUNT(*) FROM dianzan d WHERE d.video_id = v.id) as like_count
                FROM video_records v
                WHERE v.id IN ({placeholders})''',
            video_ids
        )
        video_details = cursor.fetchall()
        
        # 组装返回数据
        video_map = {}
        for v in video_details:
            video_map[v[0]] = {
                'video_id': v[0],
                'title': v[1] or '未知标题',
                'cover': v[2] or f'static/images/default-cover.jpg',
                'duration': '--:--',  # 数据库没有时长字段，给默认值
                'view_count': v[3] or 0,
                'like_count': v[4] or 0,
                'stats': f"📊 {v[3] or 0}播放 · {v[4] or 0}点赞"
            }
        
        # 按收藏时间排序
        videos = []
        for video_id in video_ids:
            if video_id in video_map:
                video = video_map[video_id].copy()
                video['collected_at'] = str(created_at_map.get(video_id, ''))
                videos.append(video)
        
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'videos': videos})
        
    except Exception as e:
        logger.error(f"获取收藏夹视频失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shoucang/folder/delete', methods=['POST'])
def delete_folder():
    """删除整个收藏夹及其下所有收藏"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    data = request.get_json()
    folder_name = data.get('folder_name')
    
    # if folder_name == '默认收藏夹':
    #     return jsonify({'success': False, 'error': '默认收藏夹不能删除'}), 400
    
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM shoucang WHERE user_id = %s AND SCname = %s',
            (user_id, folder_name)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'message': f'已删除收藏夹"{folder_name}"'})
    except Exception as e:
        logger.error(f"删除收藏夹失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/shoucang/folders', methods=['GET'])
def get_shoucang_folders():
    """获取当前用户的所有收藏夹名称（用于下拉选择）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'folders': ['默认收藏夹']})
    user_id = session['user_id']
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT DISTINCT SCname FROM shoucang WHERE user_id = %s ORDER BY SCname',
            (user_id,)
        )
        folders = []
        for row in cursor.fetchall():
            folder_name = row[0]
            # 查询视频数量
            cursor.execute(
                'SELECT COUNT(*) FROM shoucang WHERE user_id = %s AND SCname = %s AND video_id != 0',
                (user_id, folder_name)
             )
            video_count = cursor.fetchone()[0]
            cursor.execute('SELECT image_url FROM folder_backgrounds WHERE user_id = %s AND folder_name = %s', (user_id, folder_name))
            bg_result = cursor.fetchone()
            bg_url = bg_result[0] if bg_result else None
            folders.append({
                 'name': folder_name,
                 'folder_name': folder_name,
                 'video_count': video_count,
                 'bg_url': bg_url
                 })
        cursor.close()
        conn.close()
        #写计算收藏夹里多少个视频view_count
        # 如果没有任何收藏夹，至少返回默认收藏夹
        if not folders:
            folders = [{'name': '默认收藏夹', 'folder_name': '默认收藏夹', 'video_count': 0}]
        return jsonify({'success': True, 'folders': folders})
    except Exception as e:
        logger.error(f"获取收藏夹失败: {e}")
        return jsonify({'success': False, 'folders': [{'name': '默认收藏夹', 'folder_name': '默认收藏夹', 'video_count': 0}]})

#检查收藏夹状态:
@app.route('/api/shoucang/status/<int:video_id>', methods=['GET'])
def get_shoucang_status(video_id):
    """检查视频在指定收藏夹中是否已收藏"""
    if 'user_id' not in session:
        return jsonify({'success': True, 'collected': False})
    user_id = session['user_id']
    sc_name = request.args.get('sc_name', '默认收藏夹')
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id FROM shoucang WHERE user_id = %s AND video_id = %s AND SCname = %s',
            (user_id, video_id, sc_name)
        )
        collected = cursor.fetchone() is not None
        cursor.close()
        conn.close()
        return jsonify({'success': True, 'collected': collected})
    except Exception as e:
        logger.error(f"获取收藏状态失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/search', methods=['POST'])
def api_recommend():
    """智能推荐API"""
    try:
        data = request.json
        query = data.get('query', '热门推荐')
        # 安全修复：top_k 由客户端提供并直接进 LIMIT，需转成整数并夹紧范围
        try:
            top_k = int(data.get('top_k', 10))
        except (TypeError, ValueError):
            top_k = 10
        top_k = max(1, min(top_k, 50))
        if not query:
            if recommender:
                results = recommender.recommend('热门推荐', top_k)
            else:
                results = []
            return jsonify({
                'success': True,
                'results': results,
                'query': query,
                'message': '热门推荐'
            })
        if recommender:
            results = recommender.recommend(query, top_k)
        else:
            results = simple_search(query, top_k)
        
        return jsonify({
            'success': True,
            'results': results,
            'query': query,
            'count': len(results)
        })
    except Exception as e:
        logger.error(f"搜索失败: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def simple_search(query, top_k=10):
    """简单降级搜索（当推荐器不可用时）"""
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    # 简单的 SQL LIKE 搜索
    cursor.execute('''
        SELECT id, zuopin_name, zuopin_url, cover_path, view_count, tags,username
        FROM video_records
        WHERE zuopin_name LIKE %s OR tags LIKE %s
        ORDER BY view_count DESC
        LIMIT %s
    ''', (f'%{query}%', f'%{query}%', top_k))
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    # 格式化结果
    for v in results:
        v['score'] = 0.8  # 给一个固定分数
        v['views'] = format_views(v.get('view_count', 0))
    return results


def format_views(count):
    """格式化浏览量"""
    if count is None:
        return "0"
    if count >= 10000:
        return f"{count/10000:.1f}w"
    return str(count)

@app.route('/api/stream/start', methods=['POST'])
@auditor_required_api
def api_stream_start():
    """启动推流API//功能还未实现

    安全修复：该接口原先**完全无鉴权** —— 匿名请求即可让服务器用 ffmpeg 把任意
    本地媒体文件推到攻击者指定的 RTMP 地址，并可无限拉起进程。现在：
      ① 仅审核员可调用；② 默认关闭，需显式设置环境变量 ENABLE_STREAM_API=1；
      ③ video_path 必须落在上传目录内且文件真实存在；④ 推流地址只允许 rtmp(s)://。
    """
    if os.environ.get('ENABLE_STREAM_API', '0') != '1':
        return jsonify({'error': '推流功能未启用（该功能尚未完成）'}), 403
    try:
        data = request.json or {}
        video_path = data.get('video_path')
        rtmp_url = data.get('rtmp_url', 'rtmp://localhost/live/stream')
        
        if not video_path:
            return jsonify({'error': '请提供视频路径'}), 400

        # 安全修复：路径必须在上传根目录内，且必须是已存在的普通文件
        upload_root = app.config['UPLOAD_DIR']
        if not ensure_inside(upload_root, video_path):
            return jsonify({'error': '非法的视频路径'}), 400
        if not os.path.isfile(video_path):
            return jsonify({'error': '视频文件不存在'}), 404
        if not str(rtmp_url).lower().startswith(('rtmp://', 'rtmps://')):
            return jsonify({'error': '非法的推流地址'}), 400

        pusher = StreamPusher()
        success = pusher.start(video_path, rtmp_url)
        
        if success:
            return jsonify({
                'status': 'streaming',
                'rtmp_url': rtmp_url,
                'message': '推流已启动'
            })
        else:
            return jsonify({'error': '推流启动失败，请检查FFmpeg是否安装'}), 500
            
    except Exception as e:
        logger.error(f"推流启动失败: {e}")
        return jsonify({'error': str(e)}), 500

#===取消关注，粉丝api===
@app.route('/api/follow/<int:followed_id>', methods=['POST'])
def toggle_follow(followed_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    follower_id = session['user_id']
    
    if follower_id == followed_id:
        return jsonify({'success': False, 'error': '不能关注自己'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        # 检查是否已关注
        cursor.execute('SELECT id FROM follows WHERE follower_id = %s AND followed_id = %s', 
                       (follower_id, followed_id))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute('DELETE FROM follows WHERE follower_id = %s AND followed_id = %s', 
                           (follower_id, followed_id))
            action = 'unfollowed'
        else:
            cursor.execute('INSERT INTO follows (follower_id, followed_id) VALUES (%s, %s)', 
                           (follower_id, followed_id))
            action = 'followed'
        
        conn.commit()
        return jsonify({'success': True, 'action': action})
    except Exception as e:
        conn.rollback()
        print(f"关注操作失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()
@app.route('/api/follow/status/<int:followed_id>', methods=['GET'])
def get_follow_status(followed_id):
    """检查关注状态"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'following': False})
    
    follower_id = session['user_id']
    
    conn = get_db()
    cursor = conn.cursor()
    
    try:
        cursor.execute('SELECT id FROM follows WHERE follower_id = %s AND followed_id = %s', 
                       (follower_id, followed_id))
        following = cursor.fetchone() is not None
        
        return jsonify({'success': True, 'following': following})
    except Exception as e:
        logger.error(f"检查关注状态失败: {e}")
        return jsonify({'success': False, 'following': False})
    finally:
        cursor.close()
        conn.close()

#==获取粉丝数和关注数==
@app.route('/api/follow/count/<int:user_id>', methods=['GET'])
def get_follow_count(user_id):
    """获取粉丝数和关注数"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 粉丝数
    cursor.execute('SELECT COUNT(*) FROM follows WHERE followed_id = %s', (user_id,))
    followers_count = cursor.fetchone()[0]
    
    # 关注数
    cursor.execute('SELECT COUNT(*) FROM follows WHERE follower_id = %s', (user_id,))
    following_count = cursor.fetchone()[0]
    
    cursor.close()
    conn.close()
    
    return jsonify({
        'success': True, 
        'followers': followers_count,
        'following': following_count
    })

@app.route('/api/follow/following/<int:user_id>', methods=['GET'])
def get_following_list(user_id):
    """获取关注列表"""
    current_user = session.get('user_id')
    
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    
    cursor.execute('''
        SELECT u.id, u.name, u.username, u.school, u.touxiang_url,
               (SELECT 1 FROM follows WHERE follower_id = %s AND followed_id = u.id) as is_following
        FROM follows f
        JOIN users u ON f.followed_id = u.id
        WHERE f.follower_id = %s
        ORDER BY f.created_at DESC
    ''', (current_user, user_id))
    
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify({'success': True, 'users': users})


@app.route('/api/follow/followers/<int:user_id>', methods=['GET'])
def get_followers_list(user_id):
    """获取粉丝列表"""
    current_user = session.get('user_id')
    
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    
    cursor.execute('''
        SELECT u.id, u.name, u.username, u.school, u.touxiang_url,
               (SELECT 1 FROM follows WHERE follower_id = %s AND followed_id = u.id) as is_following
        FROM follows f
        JOIN users u ON f.follower_id = u.id
        WHERE f.followed_id = %s
        ORDER BY f.created_at DESC
    ''', (current_user, user_id))
    
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return jsonify({'success': True, 'users': users})
# ============= 作品管理 API =============

@app.route('/api/user/videos/<int:user_id>', methods=['GET'])
def get_user_videos(user_id):
    """获取用户已发布的作品"""
    try:
        conn = get_db()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        
        cursor.execute('''
            SELECT id, user_id, zuopin_name, cover_path, zuopin_url, view_count, upload_time, audit_result
            FROM video_records
            WHERE user_id = %s
            ORDER BY upload_time DESC
        ''', (user_id,))
        
        videos = cursor.fetchall()
        cursor.close()
        conn.close()
        
        return jsonify({'success': True, 'videos': videos})
    except Exception as e:
        logger.error(f"获取用户作品失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/video/<int:video_id>', methods=['DELETE'])
def delete_video_api(video_id):
    """删除作品"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    # 检查权限
    cursor.execute('SELECT * FROM video_records WHERE id = %s AND user_id = %s', (video_id, user_id))
    video = cursor.fetchone()
    
    if not video:
        cursor.close()
        conn.close()
        return jsonify({'success': False, 'error': '作品不存在或无权限'}), 403
    
    # 删除相关数据//得先删完其他关联数据才能删除！！！！
    cursor.execute('DELETE FROM notifications WHERE video_id = %s', (video_id,))
    cursor.execute('DELETE FROM comments WHERE video_id = %s', (video_id,))
    cursor.execute('DELETE FROM dianzan WHERE video_id = %s', (video_id,))
    cursor.execute('DELETE FROM shoucang WHERE video_id = %s', (video_id,))
    cursor.execute('DELETE FROM lishi WHERE video_id = %s', (video_id,))
    cursor.execute('DELETE FROM video_records WHERE id = %s', (video_id,))
    conn.commit()
    cursor.close()
    conn.close()
    
    # 删除视频文件（可选）
    # 安全修复：原实现直接 rmtree(dirname(zuopin_path))，若历史数据少一层目录就会删掉整个 uploads
    try:
        if video.get('zuopin_path'):
            upload_root = app.config['UPLOAD_DIR']
            target_dir = os.path.dirname(video['zuopin_path'])
            if (ensure_inside(upload_root, target_dir)
                    and os.path.abspath(target_dir) != os.path.abspath(upload_root)):
                shutil.rmtree(target_dir, ignore_errors=True)
            else:
                logger.warning(f"拒绝删除越界目录: {target_dir}")
    except Exception as e:
        logger.warning(f"删除视频目录失败: {e}")
    
    return jsonify({'success': True, 'message': '删除成功'})

@app.route('/api/video/<int:video_id>/status', methods=['POST'])
def update_video_status(video_id):
    """更新作品状态（下架）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '请先登录'}), 401
    
    user_id = session['user_id']
    data = request.get_json()
    status = data.get('status', 'hidden')

    # 安全修复：状态值原先可以是任意字符串并直接写库，改为白名单
    if status not in ('visible', 'hidden', 'pending'):
        return jsonify({'success': False, 'error': '非法的状态值'}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE video_records SET audit_result = %s WHERE id = %s AND user_id = %s',
                   (status, video_id, user_id))
    conn.commit()
    cursor.close()
    conn.close()
    
    return jsonify({'success': True, 'message': '状态更新成功'})
# ============= 你原有的其他路由 =============

@app.route('/video')
def video_page():
    """视频详情页"""
    video_id = request.args.get('id')
    if not video_id:
        return redirect(url_for('video_lost'))
    return render_template('video.html', video_id=video_id)

@app.route('/video_lost')
def video_lost():
    return render_template('videolost.html')
#查询userid如果在审核员库里，那么审核视频display->inline-block,申请审核员display->none

@app.route('/api/check_shenheyuan')
def check_shenheyuan():
    if 'user_id' not in session:
        return jsonify({'is_shenheyuan': False})
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM auditor WHERE user_id = %s', (user_id,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    return jsonify({'is_shenheyuan': result is not None})

@app.route('/shenheyuan', methods=['POST'])
@login_required_api
def shenheyuan():
    # 安全修复：这是「状态变更」接口（把用户写进 auditor 表），原先走 GET，
    # 一个 <img src="/shenheyuan"> 就能借受害者身份触发。改为 POST。
    user_id = session['user_id']
    conn = get_db()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    # 1. 查询用户注册时间
    cursor.execute('SELECT created_at FROM users WHERE id = %s', (user_id,))
    user = cursor.fetchone()
    if not user:
        cursor.close()
        conn.close()
        return jsonify({'status': 'error', 'message': '用户不存在'}), 404
    created_at = user['created_at']
    days_since_register = (datetime.now() - created_at).days
    # 2. 判断是否满30天
    if days_since_register < 30:
        remaining = 30 - days_since_register
        cursor.close()
        conn.close()
        return jsonify({
            'status': 'error', 
            'message': f'注册未满30天（已{days_since_register}天，还需{remaining}天）'
        }), 403
    # 4. 已是审核员则直接返回（幂等）
    cursor.execute('SELECT 1 FROM auditor WHERE user_id = %s', (user_id,))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        return jsonify({'status': 'success', 'message': '你已经是审核员了'})

    # 5. 安全修复：不再「满 30 天即自动获得审核权限」。
    #    默认只登记申请，权限必须由管理员在数据库里审批；
    #    仅当显式开启 ALLOW_SELF_AUDITOR=1（本地演示）时才保留原来的自助开通行为。
    if app.config.get('ALLOW_SELF_AUDITOR'):
        cursor.execute('INSERT INTO auditor (user_id) VALUES (%s)', (user_id,))
        conn.commit()
        cursor.close()
        conn.close()
        logger.warning(f"自助开通审核员(ALLOW_SELF_AUDITOR=1): user_id={user_id}")
        return jsonify({'status': 'success',
                        'message': f'注册已满{days_since_register}天，已开通审核员（自助模式）'})

    cursor.execute('''
        INSERT INTO auditor_apply (user_id, status) VALUES (%s, 'pending')
        ON DUPLICATE KEY UPDATE status = IF(status = 'rejected', 'pending', status)
    ''', (user_id,))
    conn.commit()
    cursor.close()
    conn.close()
    logger.warning(f"收到审核员申请: user_id={user_id} "
                   f"username={session.get('username')} 注册天数={days_since_register}")
    return jsonify({'status': 'success',
                    'message': f'注册已满{days_since_register}天，申请已提交，等待管理员开通'
                               f'（不会自动获得审核权限）'})

@app.route('/zhuye')
def zhuye():
    return render_template('zhuye.html')


@app.route('/zhuyedianzan')
def dianzan():
    return render_template('zhuyedianzan.html')


@app.route('/zhuyedongtai')
def dongtai():
    return render_template('zhuyedongtai.html')


@app.route('/zhuyelishi')
def lishi():
    return render_template('zhuyelishi.html')

@app.route('/zhuyetougao')
def zhuyetougao():
    return render_template('zhuyetougao.html')

@app.route('/zhuyeshoucang')
def shoucang():
    return render_template('zhuyeshoucang.html')


@app.route('/gonggao1')
def gonggao1():
    return render_template('gonggao1.html')


@app.route('/gonggao2')
def gonggao2():
    return render_template('gonggao2.html')


@app.route('/gonggao3')
def gonggao3():
    return render_template('gonggao3.html')


@app.route('/infor')
def infor():
    return render_template('infor.html')


@app.route('/gamethink')
def gamethink():
    return render_template('gamethink.html')


@app.route('/classplay')
def classplay():
    return render_template('classplay.html')


@app.route('/inforstay')
def inforstay():
    return render_template('inforstay.html')


@app.route('/tougao')
def tougao():
    return render_template('tougao.html')


@app.route('/thesis')
def thesis():
    return render_template('thesis.html')

@app.route('/search')
def search():
    return render_template('search.html')

@app.route('/shipin1')
def shipin1():
    return render_template('shipin1.html')

@app.route('/shipin2')
def shipin2():
    return render_template('shipin2.html')

@app.route('/shipin3')
def shipin3():
    return render_template('shipin3.html')

@app.route('/shipin4')
def shipin4():
    return render_template('shipin4.html')

@app.route('/shipin5')
def shipin5():
    return render_template('shipin5.html')

@app.route('/duoshipin')
def duoshipin():
    return render_template('duoshipin.html')

@app.route('/hot')
def hot():
    return render_template('hot.html')

@app.route('/biaoqian')
def biaoqian():
    biaoqian=request.args.get('label')
    tags_map = {
        'schoolife': ['教室', '宿舍', '食堂', '图书馆', '社团活动', '日常点滴'],
        'schoolperform': ['歌唱', '舞蹈', '话剧', '乐器演奏', '朗诵', '文艺汇演'],
        'schoolexercise': ['跑步', '篮球', '足球', '羽毛球', '健身', '运动会'],
        'schoolfestival': ['校庆', '元旦', '五四', '中秋', '国庆', '毕业季']
    }
    if biaoqian in tags_map:
        return jsonify({'status':'success',
                        'label':biaoqian,
                        'tags':tags_map[biaoqian]})
    else:
        return jsonify({
            'status':'error',
            'message':'？？？',
            'tags':[]
        })
@app.route('/NOAIshenhe')
def NOAIshenhe():
    # 安全修复：审核员工作台页面，非审核员不再放行
    if 'user_id' not in session:
        return redirect(url_for('loging'))
    if not is_auditor():
        flash('该页面仅审核员可访问')
        return redirect(url_for('index'))
    return render_template('NOAIshenhe.html')
@app.route('/NOAIshenhe/<int:id>')
def shenhe_video(id):
    # 安全修复：单条待审记录也属于审核员数据
    if 'user_id' not in session:
        return jsonify({'error': '请先登录'}), 401
    if not is_auditor():
        return jsonify({'error': '需要审核员权限'}), 403
    conn=get_db()
    cursor=conn.cursor(pymysql.cursors.DictCursor)
    cursor.execute("SELECT * FROM NOAI_shenhe WHERE id=%s",(id,))
    shenhe=cursor.fetchone()
    cursor.close()
    conn.close()
    if shenhe:
        return jsonify({'shenhe':shenhe})
    return jsonify({'error':'未查找成功'})
@app.route('/NOAIshenhe', methods=['POST'])
def submit_review():
    tag = request.form.get('tag')
    print(f'收到审核提交，标签分类: {tag}')
    return '提交成功'

@app.route('/shenqing')
def shenqing():

    return render_template('shenqing.html')
# @app.route('/bmsq')
# def baoming():
#     bmsq = request.args.get('baoming')
#     if bmsq == "aqtw":
#         return redirect('https://aqtwwx.qq.com/')
#     if bmsq == "MC":
#         return redirect('https://www.minecraft.net/zh-hans')
#     if bmsq == "CS":
#         return redirect('https://www.csgo.com.cn/show/index.html')
#     return render_template('bmsq.html')


# ============= 统一错误页 =============
@app.errorhandler(404)
def _handle_404(e):
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': '接口不存在'}), 404
    return render_template('404.html'), 404


@app.errorhandler(500)
def _handle_500(e):
    logger.error(f"未捕获异常: {request.method} {request.path}")
    if request.path.startswith('/api/'):
        return jsonify({'success': False, 'error': '服务内部错误，请稍后重试'}), 500
    return render_template('500.html'), 500


# ============= 健康检查 =============
@app.route('/healthz')
def healthz():
    """轻量健康检查：连库失败时返回 503，便于判断"到底哪一层没起来"。"""
    db_ok = get_db() is not None
    return jsonify({
        'status': 'ok' if db_ok else 'degraded',
        'db': db_ok,
        'ai_auditor': video_auditor is not None,
        'recommender': recommender is not None,
        'base_dir': app.root_path,
        'upload_dir': app.config['UPLOAD_DIR'],
    }), (200 if db_ok else 503)


# ============= 启动前创建表 =============
# 修复：原实现任一步失败都会让进程直接崩（数据库没起来 = 网站起不来）。
# 现在每一步独立容错，缺失时降级运行，状态由 /healthz 暴露。
with app.app_context():
    try:
        create_users_table()
    except Exception as e:
        logger.error(f"建表失败（服务仍会启动，但相关功能不可用）: {e}")
    load_videos_to_recommender()
    try:
        db.create_all()
    except Exception as e:
        logger.error(f"SQLAlchemy create_all 失败（可忽略，表已由原生 SQL 建好）: {e}")

if __name__ == '__main__':
    _host = app.config.get('HOST', '127.0.0.1')
    _port = app.config.get('PORT', 5000)
    print(f"启动服务： http://{_host}:{_port}   （健康检查 /healthz）")
    app.run(host=_host, port=_port, debug=app.config.get('DEBUG', False))