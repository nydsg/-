"""开发入口：python run.py"""
import logging

from app import create_app
from app.config import Config

app = create_app()

if __name__ == '__main__':
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    print('=' * 58)
    print(f'  校园 AI 内容共治社区 · 后端已启动')
    print(f'  http://{Config.HOST}:{Config.PORT}')
    print(f'  健康检查: http://{Config.HOST}:{Config.PORT}/api/health')
    print('=' * 58)
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG,
            threaded=True, use_reloader=False)
