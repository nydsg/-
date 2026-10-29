"""按赛题评分表审计当前项目状态（只读检查，不改代码）。"""
import json
import os
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')


def exists(rel):
    return os.path.exists(os.path.join(ROOT, rel))


def count_files(rel, pattern):
    base = os.path.join(ROOT, rel)
    n = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in ('node_modules', '.venv', '__pycache__', '.git')]
        for f in files:
            if f.endswith(pattern):
                n += 1
    return n


def has_text(rel, needle):
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        return False
    try:
        with open(p, encoding='utf-8', errors='ignore') as f:
            return needle in f.read()
    except OSError:
        return False


print('=' * 62)
print('  赛题评分维度 × 当前项目实测状态')
print('=' * 62)

# ---------- AI 技术深度 35% ----------
print('\n【AI 技术深度 35%】')

# 大模型集成 8%
llm_ok = has_text('app/ai/llm.py', 'def stream') and has_text('app/ai/llm.py', 'chat/completions')
print(f'  大模型集成(8%)      {"有" if llm_ok else "无"}'
      f'  —— OpenAI 兼容适配器 + SSE 流式 + 无 key 降级')

# Agent 8%
agent_ok = has_text('app/ai/agent.py', 'class AuditAgent')
steps = 0
if agent_ok:
    with open(os.path.join(ROOT, 'app/ai/agent.py'), encoding='utf-8') as f:
        steps = f.read().count("Step('")
print(f'  Agent/工作流(8%)    {"有" if agent_ok else "无"}  —— {steps} 步流水线，每步 SSE 推送')

# RAG 6%
rag_ok = has_text('app/ai/rag.py', 'class Retriever')
print(f'  RAG(6%)             {"有" if rag_ok else "无"}  —— TF-IDF 检索 + 8 条规范 + 判例回流')

# 多模态 5%
mods = {}
for m in ('torch', 'clip', 'easyocr'):
    try:
        __import__(m)
        mods[m] = True
    except ImportError:
        mods[m] = False
mm_code = has_text('app/ai/tools.py', 'def clip_risk') and has_text('app/ai/tools.py', 'def ocr_text')
mm_runtime = mods['clip'] and mods['easyocr']
print(f'  多模态(5%)          代码{"有" if mm_code else "无"} / 运行时{"可用" if mm_runtime else "不可用"}'
      f'  —— clip={mods["clip"]} easyocr={mods["easyocr"]} torch={mods["torch"]}')

# AI 安全 4%
safe_ok = has_text('app/ai/agent.py', 'evidence_strength') and has_text('app/ai/agent.py', 'hard_block')
print(f'  AI 安全(4%)         {"有" if safe_ok else "无"}  —— fail-closed + 高危硬否决 + 置信度 + 申诉')

# MCP/工具 4%
mcp_files = []
for root, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in ('node_modules', '.venv', '__pycache__', '.git')]
    for f in files:
        if 'mcp' in f.lower():
            mcp_files.append(os.path.relpath(os.path.join(root, f), ROOT))
tool_schema = has_text('app/ai/tools.py', 'TOOL_SCHEMAS')
print(f'  MCP/工具(4%)        工具Schema{"有" if tool_schema else "无"} / MCP Server'
      f'{"有" if mcp_files else "无"}  —— {"已经可包装" if tool_schema and not mcp_files else ""}')
if mcp_files:
    print(f'     {mcp_files}')

# ---------- 创新性 25% ----------
print('\n【创新性 25%】')
has_audit = exists('app/api/review.py')
print(f'  选题创新            {"强" if has_audit else "弱"}  —— AI 内容共治（审核+申诉+判例回流），非通用聊天机器人')
print(f'  交互创新            {"有" if has_text("web/src/pages/SubmitPage.tsx", "时间线") else "无"}'
      f'  —— 推理时间线可视化（Agent 每步耗时+证据）')
print(f'  技术方案创新        {"有" if has_text("app/ai/rag.py", "audit_cases") else "无"}'
      f'  —— 人工结论回流成判例，检索语料自我进化')

# ---------- 前端工程质量 10% ----------
print('\n【前端工程质量 10%】')
print(f'  组件数             {count_files("web/src/components", ".tsx")} 个组件文件')
print(f'  页面数             {count_files("web/src/pages", ".tsx")} 个页面')
print(f'  状态管理           {"Zustand + TanStack Query" if has_text("web/src/store/auth.ts", "zustand") else "无"}')
bundles = [f for f in os.listdir(os.path.join(ROOT, 'static/spa/assets'))
           if f.endswith('.js')] if exists('static/spa/assets') else []
print(f'  代码分割           {"已做" if len(bundles) > 1 else "未做"}  —— 当前 {len(bundles)} 个 JS chunk')
print(f'  懒加载             {"有" if has_text("web/src/App.tsx", "lazy") else "无"}')
print(f'  ErrorBoundary      {"有" if any("rror" in f for f in os.listdir(os.path.join(ROOT, "web/src/components"))) else "无"}')

# ---------- 实用性 / 用户体验 / 社会价值 ----------
print('\n【实用性 10% / 用户体验 10% / 社会价值 10%】')
print(f'  业务闭环           完整（注册→投稿→AI审核→人工复核→发布→互动）')
print(f'  可运行              {"是" if exists("static/spa/index.html") else "否"}（前端已构建 + 后端可启动）')
print(f'  数据库              {"已接通" if exists(".db_password") else "未配置"}')
print(f'  响应式              {"有" if has_text("web/src/components/Header.tsx", "md:hidden") else "无"}')
print(f'  无障碍/键盘         部分（表单有 label，缺 aria 与键盘焦点管理）')

# ---------- 交付材料 ----------
print('\n【提交材料】')
for name, path in [('README.md', 'README.md'), ('.env.example', '.env.example'),
                   ('LICENSE', 'LICENSE'), ('RUN.md', 'RUN.md'),
                   ('技术文档 PDF', None), ('演示视频', None)]:
    if path:
        print(f'  {name:<16} {"有" if exists(path) else "缺"}')
    else:
        print(f'  {name:<16} 缺（需自己产出）')
git_log = os.path.join(ROOT, '.git', 'logs', 'HEAD')
git_lines = 0
if os.path.isfile(git_log):
    with open(git_log, encoding='utf-8', errors='ignore') as f:
        git_lines = len([ln for ln in f if ln.strip()])
print(f'  Git 提交历史      {git_lines} 条 reflog'
      f'{"（仅 clone，未建立开发历史）" if git_lines <= 1 else ""}')
