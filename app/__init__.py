"""
Campus AI Web —— Flask 后端

架构（对应赛题：前端为主应用，后端仅作 AI 能力接入层）：
    app/
    ├── create_app()      应用工厂
    ├── config.py         配置（环境变量优先）
    ├── db.py             数据访问（原生 pymysql，保持与旧库兼容）
    ├── security.py       鉴权 / 校验 / 文件名清洗
    ├── api/              REST 蓝图
    │   ├── auth.py       登录注册会话
    │   ├── videos.py     作品列表 / 详情 / 投稿 / 播放量
    │   ├── social.py     点赞收藏评论关注通知
    │   ├── profile.py    个人主页与设置
    │   ├── review.py     人工审核台
    │   └── ai.py         ★ AI 能力：流式对话 / 审核 Agent / RAG（SSE）
    └── ai/               AI 层实现
        ├── llm.py        大模型适配器（OpenAI 兼容协议 + 无 key 降级）
        ├── tools.py      Agent 可调用工具（抽帧 / CLIP / OCR / 法规检索）
        ├── agent.py      审核 Agent 编排（规划 → 调用 → 决策）
        └── rag.py        规范与判例检索
"""
from .factory import create_app

__all__ = ['create_app']
