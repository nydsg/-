"""切片审核接口验证：切几段、每段结论、聚合结论、是否落库。"""
import json
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import requests

BASE = 'http://127.0.0.1:8000'
s = requests.Session()
s.trust_env = False
r = s.post(f'{BASE}/api/auth/login',
           json={'username': 'demo', 'password': '123456'}, timeout=10)
print('登录:', r.status_code)

st = s.get(f'{BASE}/api/ai/status', timeout=10).json()
print('能力:', st.get('capabilities'))
print()

for name in (sys.argv[1:] or ['case_mixed.mp4', 'case_normal.mp4']):
    print('=' * 60)
    print(name)
    print('=' * 60)
    plan = None
    segs = []
    result = None
    err = None
    with s.post(f'{BASE}/api/ai/audit/segment/stream',
                json={'filename': name}, stream=True,
                timeout=(10, 300)) as resp:
        print('  HTTP:', resp.status_code)
        if resp.status_code != 200:
            print(' ', resp.text[:200])
            continue
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith('data: '):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            t = ev.get('type')
            if t == 'plan':
                plan = ev['plan']
                print(f"  切片计划: 时长 {plan['duration']}s → {plan['count']} 段 "
                      f"(每段约 {plan['segment_seconds']}s)")
            elif t == 'segment':
                g = ev['segment']
                segs.append(g)
                risk = '、'.join(f"{r.get('type')}({r.get('severity')})"
                                 for r in g.get('risks') or []) or '-'
                print(f"    [{g['start']:>4}–{g['end']:>4}s] {g['verdict']:<7} "
                      f"score={g['score']:<3} 场景={g.get('scene') or '?':<8} 风险={risk}")
            elif t == 'result':
                result = ev
            elif t == 'error':
                err = ev.get('message')
            elif t == 'done':
                break

    if err:
        print('  ERROR:', err)
        continue
    if result:
        print()
        print(f"  整体: {result['verdict']}  score={result['score']}  "
              f"证据强度={result.get('evidence_strength')}  耗时={result.get('elapsed_ms')}ms")
        print(f"  命中类别: {result.get('categories')}")
        for rsn in (result.get('reasons') or [])[:3]:
            print(f"    · {rsn[:80]}")
        if result.get('narrative'):
            print(f"  总结: {result['narrative'][:150]}")
        print(f"  时间轴段数: {len(result.get('segments') or [])}")
