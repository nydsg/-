"""MCP Server 协议级探针：不做猜测，真的按 JSON-RPC 握手一次。

用法：.venv\\Scripts\\python.exe tools\\mcp_probe.py

流程：
    1) 以子进程方式拉起 mcp_server.py（stdio 传输）
    2) 发送 initialize 握手
    3) 发送 tools/list 列出工具
    4) 调用一次 rule_lookup，验证工具真的能跑
    5) 打印结果并退出

为什么需要它：stdio 服务器在没有客户端时会因为 stdin EOF 立刻退出，
用 Start-Process 之类的"看进程活没活"的方式验证是错的，会得到假阴性。
"""
import json
import os
import subprocess
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
SERVER = os.path.join(ROOT, 'mcp_server.py')

PROTOCOL_VERSION = '2024-11-05'


def rpc(proc, msg: dict) -> dict | None:
    """写入一行 JSON-RPC 并读回一行响应。"""
    proc.stdin.write(json.dumps(msg, ensure_ascii=False) + '\n')
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        # 服务端可能先打了日志行，跳过非 JSON 行再读一次
        for _ in range(3):
            line = proc.stdout.readline()
            if not line:
                return None
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def main() -> int:
    if not os.path.isfile(PY):
        print(f'找不到解释器：{PY}')
        return 2

    env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    proc = subprocess.Popen(
        [PY, SERVER],
        cwd=ROOT, env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', bufsize=1,
    )

    ok = 0
    total = 0
    try:
        # --- 1. 握手 ---
        total += 1
        resp = rpc(proc, {
            'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
            'params': {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {},
                'clientInfo': {'name': 'mcp-probe', 'version': '1.0'},
            },
        })
        if resp and 'result' in resp:
            info = resp['result'].get('serverInfo') or {}
            print(f'  OK   initialize → {info.get("name", "?")} '
                  f'v{info.get("version", "?")} '
                  f'(protocol {resp["result"].get("protocolVersion", "?")})')
            ok += 1
        else:
            print(f'  FAIL initialize → {resp}')

        # 通知：握手完成
        proc.stdin.write(json.dumps({'jsonrpc': '2.0',
                                     'method': 'notifications/initialized'}) + '\n')
        proc.stdin.flush()

        # --- 2. 列工具 ---
        total += 1
        resp = rpc(proc, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}})
        tools = (resp or {}).get('result', {}).get('tools', [])
        if tools:
            print(f'  OK   tools/list → {len(tools)} 个工具')
            for t in tools:
                print(f'         - {t["name"]}')
            ok += 1
        else:
            print(f'  FAIL tools/list → {resp}')

        # --- 3. 真调一个工具 ---
        total += 1
        resp = rpc(proc, {
            'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
            'params': {'name': 'rule_lookup',
                       'arguments': {'query': '成人礼 仪式', 'top_k': 2}},
        })
        content = (resp or {}).get('result', {}).get('content') or []
        text = content[0].get('text', '') if content else ''
        if 'R2-2' in text or '成人礼' in text:
            print('  OK   tools/call rule_lookup → 命中预期条款')
            print('       ' + text[:120].replace('\n', ' '))
            ok += 1
        else:
            print(f'  FAIL tools/call → {(resp or {}).get("error") or text[:150]}')

    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print(f'\n结果：{ok}/{total} 通过')
    if ok != total:
        err = proc.stderr.read() if proc.stderr else ''
        if err:
            print('server stderr:\n' + err[-800:])
    return 0 if ok == total else 1


if __name__ == '__main__':
    sys.exit(main())
