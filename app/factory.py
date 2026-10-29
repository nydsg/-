"""应用工厂：注册蓝图、统一错误处理、托管 SPA、兼容旧静态路由。"""
import logging
import os

from flask import Flask, jsonify, request, send_from_directory

from .config import Config
from .db import DatabaseUnavailable, ensure_schema, ping

logger = logging.getLogger(__name__)


def create_app(config_object=Config) -> Flask:
    app = Flask(
        __name__,
        static_folder=os.path.join(Config.SPA_DIR, 'assets'),
        static_url_path='/assets',
    )
    app.config.from_object(config_object)
    app.json.ensure_ascii = False          # 中文直接输出，便于前端调试
    app.json.sort_keys = False

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    _register_blueprints(app)
    _register_files(app)
    _register_spa(app)
    _register_errors(app)
    _bootstrap(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from .api import ai, auth, profile, review, social, videos

    app.register_blueprint(auth.bp, url_prefix='/api/auth')
    app.register_blueprint(videos.bp, url_prefix='/api')
    app.register_blueprint(social.bp, url_prefix='/api')
    app.register_blueprint(profile.bp, url_prefix='/api')
    app.register_blueprint(review.bp, url_prefix='/api')
    app.register_blueprint(ai.bp, url_prefix='/api/ai')


def _register_files(app: Flask) -> None:
    """媒体/静态文件服务：全部走配置目录，不写死盘符。"""

    @app.get('/uploads/<path:filename>')
    def serve_upload(filename):
        return send_from_directory(app.config['UPLOAD_DIR'], filename)

    @app.get('/media/noai/<path:filename>')
    def serve_noai(filename):
        return send_from_directory(app.config['NOAI_DIR'], filename)

    @app.get('/media/avatar/<path:filename>')
    def serve_avatar(filename):
        return send_from_directory(app.config['AVATAR_DIR'], filename)

    @app.get('/media/bg/<path:filename>')
    def serve_bg(filename):
        return send_from_directory(app.config['BG_DIR'], filename)

    @app.get('/media/dynamic/<path:filename>')
    def serve_dynamic(filename):
        return send_from_directory(app.config['DYNAMIC_DIR'], filename)

    @app.get('/media/favbg/<path:filename>')
    def serve_favbg(filename):
        return send_from_directory(app.config['FAV_BG_DIR'], filename)


def _register_spa(app: Flask) -> None:
    """前端为 SPA：非 /api 的路径交给 index.html，由前端路由接管。

    注意顺序：必须先尝试 static 目录下的真实文件（图片/字体/构建产物），
    否则通配路由会把 /static/xxx 也返回成 index.html —— 这会让页面白屏。
    """
    index_file = os.path.join(app.config['SPA_DIR'], 'index.html')
    static_root = os.path.join(Config.BASE_DIR, 'static') if hasattr(Config, 'BASE_DIR') \
        else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')

    @app.get('/')
    @app.get('/<path:path>')
    def spa(path: str = ''):
        if path.startswith('api/'):
            return jsonify({'success': False, 'error': '接口不存在'}), 404

        if path:
            # 1) 构建产物（/assets/... 由 Flask static_url_path 处理，这里兜底 SPA 目录）
            candidate = os.path.join(app.config['SPA_DIR'], path)
            if os.path.isfile(candidate):
                return send_from_directory(app.config['SPA_DIR'], path)
            # 2) 原项目静态资源（/static/jpg/home.jpg 这类）
            if path.startswith('static/'):
                rel = path[len('static/'):]
                target = os.path.join(static_root, rel)
                if os.path.isfile(target):
                    return send_from_directory(static_root, rel)

        if not os.path.isfile(index_file):
            return jsonify({
                'success': False,
                'error': '前端尚未构建：请先在 web/ 目录执行 npm run build',
            }), 503
        return send_from_directory(app.config['SPA_DIR'], 'index.html')


def _register_errors(app: Flask) -> None:
    @app.errorhandler(DatabaseUnavailable)
    def _db_down(e):
        logger.error('数据库不可用: %s', e)
        if request.path.startswith('/api/'):
            return jsonify({
                'success': False,
                'error': '数据库暂不可用：请确认 MySQL 已启动、库已创建、口令正确（见 /api/health）',
            }), 503
        return jsonify({'success': False, 'error': '数据库暂不可用'}), 503

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': '接口不存在'}), 404
        return jsonify({'success': False, 'error': '页面不存在'}), 404

    @app.errorhandler(500)
    def _500(e):
        logger.exception('未捕获异常: %s %s', request.method, request.path)
        if request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': '服务内部错误'}), 500
        return jsonify({'success': False, 'error': '服务内部错误'}), 500


def _bootstrap(app: Flask) -> None:
    """启动自检：目录、建表、AI 状态。任何一步失败都不阻止服务启动。"""
    for d in app.config.get('ALL_DIRS', []):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError as e:
            logger.warning('目录创建失败 %s: %s', d, e)

    # 启动自检结果：供 /api/health 暴露，避免"表没建但界面看不出来"
    schema_state = {'ok': False, 'error': '数据库不可用', 'tables': 0}

    if ping():
        try:
            created = ensure_schema()
            schema_state = {'ok': True, 'error': '', 'tables': len(created or [])}
            logger.info('数据库就绪，表结构已校验（%s 张表）', schema_state['tables'])
        except Exception as e:
            # 建表失败必须明确暴露：否则会出现"db=true 但一张表都没有"的假健康
            schema_state = {'ok': False, 'error': str(e), 'tables': 0}
            logger.error('表结构创建失败，服务以降级模式启动: %s', e)
    else:
        logger.warning('数据库不可用：服务将以降级模式启动，AI 与业务接口返回 503')

    from .ai.llm import LLMClient
    client = LLMClient()
    if client.enabled:
        logger.info('AI 已启用：%s / %s', client.base_url, client.model)
    else:
        logger.warning('AI 未启用（缺 AI_API_KEY）：AI 接口将返回演示数据')

    @app.get('/api/health')
    def health():
        from .ai.llm import LLMClient as _LLM
        db_ok = ping()
        healthy = bool(db_ok and schema_state['ok'])
        return jsonify({
            'status': 'ok' if healthy else 'degraded',
            'db': db_ok,
            'schema_ok': schema_state['ok'],
            'schema_error': schema_state['error'],
            'tables': schema_state['tables'],
            'ai': _LLM().enabled,
            'model': app.config.get('AI_MODEL') if _LLM().enabled else '',
            'spa': os.path.isfile(os.path.join(app.config['SPA_DIR'], 'index.html')),
        }), (200 if healthy else 503)
