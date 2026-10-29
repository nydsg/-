"""
MCP Server —— 把平台的内容审核能力以 Model Context Protocol 对外暴露。

为什么单独做一个进程：
    Flask 里的审核工具只能被自己的路由调用；MCP 是标准协议，任何支持它的客户端
    （Claude Desktop / Cursor / Windsurf / 其他 Agent）都能发现并调用这 6 个工具，
    而且**不需要启动 Web 服务、不需要登录、不需要数据库**。

用法：
    1) 安装依赖
         .venv\\Scripts\\python.exe -m pip install -r requirements-mcp.txt
    2) 自检（不连接宿主，直接跑一遍工具，确认环境可用）
         .venv\\Scripts\\python.exe mcp_server.py --selftest
    3) 以 stdio 方式启动（由 MCP 客户端拉起）
         .venv\\Scripts\\python.exe mcp_server.py

客户端配置见 mcp.json.example。

工具清单（与 app/ai/tools.py 的 REGISTRY 一一对应）：
    extract_frames  抽关键帧
    clip_risk       视觉风险打分
    ocr_text        画面文字识别
    rule_lookup     规范与判例检索
    keyword_risk    关键词风险匹配
    meta_risk       文件名可疑信号
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from app.ai import tools  # noqa: E402


def _dump(name: str, payload: dict) -> str:
    """统一返回 JSON 字符串：MCP 客户端按文本渲染，结构化内容更易读。"""
    try:
        return json.dumps({'tool': name, **payload}, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return json.dumps({'tool': name, 'ok': False, 'error': '结果无法序列化'},
                          ensure_ascii=False)


def register(mcp) -> None:
    """把 6 个工具注册到 MCP server 上。"""

    @mcp.tool()
    def extract_frames(video_path: str, max_frames: int = 15) -> str:
        """从本地视频文件等间隔抽取若干关键帧，返回帧图片的路径列表。

        Args:
            video_path: 视频文件的绝对路径（mp4 / mov）
            max_frames: 抽帧数量，默认 15
        """
        return _dump('extract_frames',
                     tools.call_tool('extract_frames',
                                     {'video_path': video_path,
                                      'max_frames': max_frames}))

    @mcp.tool()
    def clip_risk(frame_paths: list[str]) -> str:
        """对关键帧做视觉风险打分，返回 正常/色情/暴力/武器/成瘾 五类相似度。

        Args:
            frame_paths: 关键帧图片路径列表（由 extract_frames 产出）
        """
        return _dump('clip_risk',
                     tools.call_tool('clip_risk', {'frame_paths': frame_paths}))

    @mcp.tool()
    def ocr_text(frame_paths: list[str]) -> str:
        """识别关键帧中的文字内容，用于发现画面上的违规文字（联系方式、二维码等）。

        Args:
            frame_paths: 关键帧图片路径列表
        """
        return _dump('ocr_text',
                     tools.call_tool('ocr_text', {'frame_paths': frame_paths}))

    @mcp.tool()
    def rule_lookup(query: str, top_k: int = 3) -> str:
        """检索校园内容规范条款与历史判例，为判定提供可引用的依据。

        Args:
            query: 检索关键词或内容摘要
            top_k: 返回条款数量，默认 3
        """
        return _dump('rule_lookup',
                     tools.call_tool('rule_lookup',
                                     {'query': query, 'top_k': top_k}))

    @mcp.tool()
    def keyword_risk(texts: list[str]) -> str:
        """对文本做违规关键词匹配，作为模型判断的交叉验证。

        Args:
            texts: 待检查的文本列表（标题、OCR 结果、简介等）
        """
        return _dump('keyword_risk',
                     tools.call_tool('keyword_risk', {'texts': texts}))

    @mcp.tool()
    def meta_risk(filename: str) -> str:
        """检查文件名中的可疑信号（如 18+、裸、破解 等）。

        Args:
            filename: 原始文件名
        """
        return _dump('meta_risk',
                     tools.call_tool('meta_risk', {'filename': filename}))


# ---------------- 自检：不依赖 MCP 宿主，直接验证工具链 ----------------
def selftest() -> int:
    print('=== 环境自检 ===')
    import importlib
    for mod in ('cv2', 'numpy', 'PIL', 'clip', 'easyocr'):
        try:
            importlib.import_module(mod)
            print(f'  {mod:<10} 可用')
        except ImportError:
            print(f'  {mod:<10} 缺失（该能力会降级）')

    print('\n=== 工具自检 ===')
    cases = [
        ('meta_risk', {'filename': 'demo_18+.mp4'}),
        ('rule_lookup', {'query': '成人礼 仪式 低俗', 'top_k': 2}),
        ('keyword_risk', {'texts': ['加微信日结 代考']}),
    ]
    ok = 0
    for name, args in cases:
        result = tools.call_tool(name, args)
        flag = 'OK  ' if result.get('ok') or result.get('flags') or result.get('hits') else 'WARN'
        if flag.strip() == 'OK':
            ok += 1
        print(f'  [{flag}] {name:<14} {json.dumps(result, ensure_ascii=False)[:110]}')

    print(f'\n{ok}/{len(cases)} 个工具返回了有效结果')
    print('提示：抽帧/视觉/OCR 需要真实视频与对应依赖，未纳入自检。')
    return 0 if ok == len(cases) else 1


def _build_server():
    """构造 MCP server，兼容 SDK 的两个大版本。

    - mcp 1.x：`from mcp.server.fastmcp import FastMCP`
    - mcp 2.x：`FastMCP` 已改名为 `MCPServer`（`mcp.server.mcpserver`）

    两者都提供 `@server.tool()` 装饰器与 `server.run()`，本文件其余逻辑完全一致，
    所以只在构造这一处做适配，避免让使用者去纠结版本。
    """
    instructions = (
        '校园内容审核工具集。典型流程：'
        'extract_frames 抽帧 → clip_risk / ocr_text 分析 → '
        'rule_lookup 检索规范与判例 → 综合给出 通过/转人工/拒绝 的结论。'
    )

    # mcp >= 2
    try:
        from mcp.server.mcpserver import MCPServer

        return MCPServer('campus-content-audit',
                         instructions=instructions,
                         version='1.0.0'), 'mcp2'
    except ImportError:
        pass

    # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP

        return FastMCP('campus-content-audit',
                       instructions=instructions), 'mcp1'
    except ImportError:
        return None, ''


def main() -> int:
    if '--selftest' in sys.argv:
        return selftest()

    server, version = _build_server()
    if server is None:
        print('缺少 mcp 依赖。请先执行：\n'
              '  .venv\\Scripts\\python.exe -m pip install -r requirements-mcp.txt',
              file=sys.stderr)
        return 2

    register(server)
    print(f'[mcp] 已启动 campus-content-audit（SDK {version}，stdio 传输）',
          file=sys.stderr)
    # 默认 stdio 传输：由客户端作为子进程拉起，无需端口，也无需鉴权
    if version == 'mcp2':
        try:
            server.run(transport='stdio')
        except TypeError:
            server.run()
    else:
        server.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
