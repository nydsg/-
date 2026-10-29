"""
配置文件

运行说明：
1. 所有目录都由项目根目录 `BASE_DIR` 派生，**不再写死任何盘符或绝对路径**，
   项目放在哪个盘、哪个目录都能跑；
2. 敏感项（SECRET_KEY / 数据库口令）从环境变量或本地未跟踪文件读取，不写进源码；
3. 数据库口令取值顺序：环境变量 `DB_PASSWORD` → 项目根目录 `.db_password` 文件 → 空
   （空值时 get_db() 会明确报错，不会静默降级）。
"""

import os
import secrets
from urllib.parse import quote_plus


# ==================== 路径（全部相对项目根目录） ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _path(*parts):
    return os.path.join(BASE_DIR, *parts)


# 各业务目录（运行期由 ensure_dirs() 创建，import 时不产生副作用）
UPLOAD_DIR = _path('uploads')                       # 已过审视频 + 封面
TEMP_DIR = _path('uploads', 'temp_video')           # 抽帧/AI 审核的临时中转
NOAI_DIR = _path('NOAIuploads')                     # 待人工审核的投稿
JPG_DIR = _path('static', 'jpg')                    # 图片类投稿
AVATAR_DIR = _path('static', 'avatars')             # 用户头像
BG_DIR = _path('static', 'backgrounds')             # 个人主页背景
DYNAMIC_DIR = _path('static', 'dynamics')           # 动态配图
FAV_BG_DIR = _path('static', 'dynamics', 'bg')      # 收藏夹背景
CAROUSEL_DIR = _path('static', 'carousel')          # 首页轮播图

_ALL_DIRS = (
    UPLOAD_DIR, TEMP_DIR, NOAI_DIR, AVATAR_DIR,
    BG_DIR, DYNAMIC_DIR, FAV_BG_DIR, CAROUSEL_DIR,
)


def ensure_dirs():
    """创建所有运行期需要的目录（幂等，启动时调用一次即可）。"""
    for d in _ALL_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            print(f"[config] 目录创建失败 {d}: {e}")


# ==================== 密钥 / 口令 ====================
def _read_text_file(filename):
    """读取项目根目录下的可选文本文件（不存在/不可读则返回空串）。"""
    try:
        with open(_path(filename), 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception:
        return ''


def _read_db_password():
    """数据库口令的取值顺序：环境变量 DB_PASSWORD → `.db_password` 文件 → 空。"""
    env_pwd = os.environ.get('DB_PASSWORD')
    if env_pwd:
        return env_pwd
    return _read_text_file('.db_password')


def _read_or_create_secret_key():
    """SECRET_KEY 的取值顺序：环境变量 SECRET_KEY → `.secret_key` 文件 → 新生成并落盘。"""
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key

    key_file = _path('.secret_key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                saved = f.read().strip()
            if saved:
                return saved
        new_key = secrets.token_hex(32)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(new_key)
        try:
            os.chmod(key_file, 0o600)
        except Exception:
            pass
        return new_key
    except Exception:
        # 落盘失败时退回进程内随机值：代价是重启后会话失效，但绝不留硬编码密钥
        return secrets.token_hex(32)


def _env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name, default=False):
    return os.environ.get(name, '1' if default else '0') == '1'


class Config:
    """基础配置"""
    SECRET_KEY = _read_or_create_secret_key()

    # 上传配置
    UPLOAD_FOLDER = UPLOAD_DIR                       # 绝对路径（与各路由保持一致）
    MAX_CONTENT_LENGTH = 500 * 1024 * 1024           # 500MB
    MAX_FRAMES = 15                                  # 最大抽帧数

    # 各业务目录，路由里统一从这里取，避免散落硬编码
    UPLOAD_DIR = UPLOAD_DIR
    TEMP_DIR = TEMP_DIR
    NOAI_DIR = NOAI_DIR
    JPG_DIR = JPG_DIR
    AVATAR_DIR = AVATAR_DIR
    BG_DIR = BG_DIR
    DYNAMIC_DIR = DYNAMIC_DIR
    FAV_BG_DIR = FAV_BG_DIR
    CAROUSEL_DIR = CAROUSEL_DIR

    # 生成媒体 URL 时使用的前缀。
    # 留空（默认）→ 生成 `/uploads/xxx` 这类相对路径，任何域名/端口/局域网访问都正确；
    # 如需绝对地址（例如 CDN），设环境变量 PUBLIC_BASE_URL=https://example.com。
    PUBLIC_BASE_URL = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')

    # 数据库配置
    DB_CONFIG = {
        'user': os.environ.get('DB_USER', 'root'),
        'password': _read_db_password(),
        'database': os.environ.get('DB_NAME', 'userdb'),
        'host': os.environ.get('DB_HOST', 'localhost'),
        'port': _env_int('DB_PORT', 3306),
    }

    # 由 DB_CONFIG 派生，避免同一份口令在代码里出现两遍
    SQLALCHEMY_DATABASE_URI = (
        "mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}".format(
            user=DB_CONFIG['user'],
            pwd=quote_plus(str(DB_CONFIG['password'])),
            host=DB_CONFIG['host'],
            port=DB_CONFIG['port'],
            db=DB_CONFIG['database'],
        )
    )

    # 审核员自助开通开关（默认关闭）。
    # 开启后「注册满 30 天」即可直接进入 auditor 表；关闭时只登记申请，
    # 需要管理员在数据库里审批。仅建议本地演示时开启。
    ALLOW_SELF_AUDITOR = _env_bool('ALLOW_SELF_AUDITOR', False)

    # 服务监听参数
    HOST = os.environ.get('HOST', '127.0.0.1')
    PORT = _env_int('PORT', 5000)


class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = _env_bool('DEBUG', True)


class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False


if __name__ == '__main__':
    # 小工具：`python config.py` 打印当前生效的配置，便于排查"到底连的哪个库"
    print(f"BASE_DIR = {BASE_DIR}")
    for name in ('UPLOAD_DIR', 'NOAI_DIR', 'AVATAR_DIR', 'BG_DIR',
                 'DYNAMIC_DIR', 'FAV_BG_DIR', 'CAROUSEL_DIR'):
        print(f"{name:<14}= {getattr(Config, name)}")
    db = Config.DB_CONFIG
    print(f"DB         = {db['user']}@{db['host']}:{db['port']}/{db['database']}"
          f" (password {'已配置' if db['password'] else '未配置'})")
    print(f"PUBLIC_BASE_URL = {Config.PUBLIC_BASE_URL or '(相对路径)'}")
