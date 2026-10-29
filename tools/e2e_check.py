"""端到端联调脚本：真实数据库 + 真实 HTTP 会话。

覆盖：登录 → 当前用户 → 投稿列表 → 作品详情 → 点赞/收藏/评论 → 审核台 → AI 审核流 → RAG 检索
运行：.venv\\Scripts\\python.exe tools\\e2e_check.py
"""
import json
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import requests  # noqa: E402

BASE = os.environ.get('BASE', 'http://127.0.0.1:8000')
PASS = FAIL = 0
FAILURES = []


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  OK   {name}')
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f'  FAIL {name}  {detail}')


s = requests.Session()
s.trust_env = False      # 关键：绕过系统代理，否则 SSE 长连接会被代理缓冲区挂住

print('== 1. 登录（demo / 123456）==')
r = s.post(f'{BASE}/api/auth/login', json={'username': 'demo', 'password': '123456'})
b = r.json()
check('登录成功', r.status_code == 200 and b.get('user', {}).get('username') == 'demo',
      f'{r.status_code} {b}')
check('demo 是审核员（种子数据设置）', b.get('user', {}).get('is_auditor') is True,
      f"{b.get('user', {}).get('is_auditor')}")

print('== 2. 当前用户 ==')
b = s.get(f'{BASE}/api/auth/me').json()
u = b.get('user') or {}
check('/auth/me 已登录', b.get('authenticated') is True, f'{b.get("authenticated")}')
check('含统计字段', all(k in u for k in ('works', 'fans', 'total_likes')), f'{list(u)}')

print('== 3. 作品列表与详情 ==')
b = s.get(f'{BASE}/api/videos?page=1&size=50').json()
# 注意：断言"总数恰好等于种子数"是脆弱的 —— 用户自己投稿后总数就会变，
# 检查会误报失败。这里改为校验"种子作品齐备 + 总数不少于种子数"。
SEED_TITLES = ['图书馆的深夜', '食堂阿姨的一天', '社团纳新现场',
               '毕业季的操场', '实验室安全演示', '元旦晚会彩排']
titles = {str(i.get('title') or i.get('name') or '') for i in (b.get('items') or [])}
check('作品总数 >= 种子数 6', (b.get('total') or 0) >= 6, f"total={b.get('total')}")
check('6 条种子作品齐备', all(t in titles for t in SEED_TITLES),
      f'缺失={[t for t in SEED_TITLES if t not in titles]}')
vid = b['items'][0]['id']
d = s.get(f'{BASE}/api/videos/{vid}').json().get('video') or {}
check('详情含统计', all(k in d for k in ('likes', 'collects', 'comment_count')), f'{list(d)}')

print('== 4. 互动：点赞 / 收藏 / 评论 ==')
b = s.post(f'{BASE}/api/videos/{vid}/like').json()
check('点赞返回 liked+count', 'liked' in b and 'count' in b, f'{b}')
b = s.post(f'{BASE}/api/videos/{vid}/collect', json={'folder': '默认收藏夹'}).json()
check('收藏返回 collected', 'collected' in b, f'{b}')
b = s.post(f'{BASE}/api/videos/{vid}/comments',
           json={'content': '这条评论来自端到端联调脚本'}).json()
check('发表评论成功', b.get('success') is True and b.get('id'), f'{b}')
b = s.get(f'{BASE}/api/videos/{vid}/comments').json()
check('评论列表已包含新评论', b.get('total', 0) >= 1, f"total={b.get('total')}")
check('评论字段与前端 Comment 类型一致',
      all(k in (b['items'][0] if b.get('items') else {})
          for k in ('id', 'content', 'author', 'replies')), '字段缺失')

print('== 5. 个人主页 ==')
b = s.get(f'{BASE}/api/users/{u.get("id", 1)}').json()
check('主页含 user/works/stats',
      all(k in b for k in ('user', 'works', 'stats')), f'{list(b)}')

print('== 6. 审核台 ==')
b = s.get(f'{BASE}/api/review/queue').json()
check('审核队列可访问', 'items' in b and 'ai_runs' in b, f'{list(b)}')
b = s.get(f'{BASE}/api/review/cases').json()
check('判例库有 3 条种子判例', len(b.get('items', [])) >= 3, f"{len(b.get('items', []))}")

print('== 7. RAG 检索（判例回流生效）==')
b = s.get(f'{BASE}/api/ai/rag/search', params={'q': '成人礼 仪式', 'k': 3}).json()
ids = [h['id'] for h in b.get('items', [])]
check('命中 R2-2 礼仪豁免', 'R2-2' in ids, f'{ids}')
check('语料含人工判例', any(h['source'] == 'case' or h['id'].startswith('C')
                            for h in b.get('items', [])) or b.get('corpus_size', 0) > 8,
      f"corpus={b.get('corpus_size')} ids={ids}")

print('== 8. AI 审核流（SSE，真实视频）==')
video_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'uploads', 'temp_video', 'demo_audit.mp4')
if os.path.isfile(video_path):
    events = []
    # 注意：SSE 迭代必须 (a) 禁用代理 (b) 有明确 read timeout (c) 遇到 done 立即停，
    # 否则 requests 会一直等连接关闭，看起来像"卡死"。
    with s.post(f'{BASE}/api/ai/audit/stream',
                json={'filename': 'demo_audit.mp4'}, stream=True,
                timeout=(10, 60)) as resp:
        check('审核流 HTTP 200', resp.status_code == 200, f'{resp.status_code}')
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith('data: '):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            events.append(ev)
            if ev.get('type') in ('done', 'error'):
                break
        resp.close()
    steps = [e for e in events if e.get('type') == 'step']
    result = next((e for e in events if e.get('type') == 'result'), None)
    check('收到 6 步事件', len(steps) >= 6, f'{len(steps)}')
    check('收到 result 事件', result is not None, '')
    if result:
        check('结论是合法枚举', result['verdict'] in ('pass', 'manual', 'reject'),
              result['verdict'])
        check('含 reasons 与 steps',
              bool(result.get('reasons')) and len(result.get('steps') or []) >= 6, '')
        print(f"       结论={result['verdict']} score={result['score']} "
              f"strength={result.get('evidence_strength')} 耗时={result['elapsed_ms']}ms")
    # 审核结果应落库
    h = s.get(f'{BASE}/api/ai/audit/history').json()
    check('审核记录已落库', len(h.get('items', [])) >= 1, f"{len(h.get('items', []))}")
else:
    print('  SKIP 测试视频不存在')

print('== 9. 未登录隔离 ==')
anon = requests.Session()
check('匿名访问审核台 401', anon.get(f'{BASE}/api/review/queue').status_code == 401)
check('匿名访问我的作品 401', anon.get(f'{BASE}/api/videos/mine').status_code == 401)

print()
print(f'结果：{PASS} 通过 / {FAIL} 失败')
if FAILURES:
    print('失败项:', ', '.join(FAILURES))
sys.exit(0 if FAIL == 0 else 1)
