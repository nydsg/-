"""验证审核台的 pending_id 切片分析链路。

流程：
  1. 上传一个待审视频（会写入 NOAI_shenhe，状态 pending）
  2. 用 pending_id 调切片审核接口
  3. 断言能正确解析到磁盘文件并完成逐段判定
  4. 清理测试数据（删待审记录 + 作品记录 + 文件）
"""
import json
import os
import shutil
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.db import use_cursor  # noqa: E402

BASE = 'http://127.0.0.1:8000'
s = requests.Session()
s.trust_env = False
print('登录:', s.post(f'{BASE}/api/auth/login',
                     json={'username': 'demo', 'password': '123456'}, timeout=10).status_code)

video = os.path.join(ROOT, 'uploads', 'temp_video', 'case_mixed.mp4')
if not os.path.isfile(video):
    print('缺少 case_mixed.mp4，请先运行 tools/make_test_videos.py')
    sys.exit(2)

print('\n1) 上传待审视频（生成 NOAI_shenhe 记录）')
with open(video, 'rb') as f:
    up = s.post(f'{BASE}/api/videos/upload',
                files={'file': ('切片待审测试.mp4', f, 'video/mp4')},
                data={'title': '切片待审测试', 'tags': '测试'},
                timeout=300)
print('   HTTP:', up.status_code)
j = up.json()
pending_id, video_id = j.get('pending_id'), j.get('video_id')
print(f'   pending_id={pending_id}  video_id={video_id}')
if not pending_id:
    print('   上传失败:', str(j)[:200])
    sys.exit(1)

print('\n2) 用 pending_id 跑切片审核')
plan = None
segs = []
result = None
err = None
with s.post(f'{BASE}/api/ai/audit/segment/stream',
            json={'pending_id': pending_id}, stream=True,
            timeout=(10, 400)) as resp:
    print('   HTTP:', resp.status_code)
    if resp.status_code != 200:
        print('   ', resp.text[:300])
        sys.exit(1)
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
            print(f"   切片计划: {plan['duration']}s → {plan['count']} 段")
        elif t == 'segment':
            segs.append(ev['segment'])
            g = ev['segment']
            print(f"     [{g['start']:>5}–{g['end']:>5}s] {g['verdict']:<7} "
                  f"场景={g.get('scene') or '?'}")
        elif t == 'result':
            result = ev
        elif t == 'error':
            err = ev.get('message')
        elif t == 'done':
            break

ok = True
if err:
    print('   ERROR:', err)
    ok = False
if not plan or not plan.get('count'):
    print('   ✗ 没有拿到切片计划')
    ok = False
if len(segs) != (plan or {}).get('count'):
    print(f"   ✗ 段数不一致：结果 {len(segs)} vs 计划 {(plan or {}).get('count')}")
    ok = False
if not result:
    print('   ✗ 没有聚合结果')
    ok = False
else:
    print(f"\n   整体: {result['verdict']} score={result['score']} "
          f"证据强度={result.get('evidence_strength')}")
    for r in (result.get('reasons') or [])[:2]:
        print('     ·', r[:80])

print('\n3) 清理测试数据')
with use_cursor() as (_c, cur):
    cur.execute('SELECT zuopin_url FROM NOAI_shenhe WHERE id = %s', (pending_id,))
    row = cur.fetchone()
    url = (row or {}).get('zuopin_url') or ''
with use_cursor(commit=True) as (_c, cur):
    cur.execute('DELETE FROM NOAI_shenhe WHERE id = %s', (pending_id,))
    if video_id:
        cur.execute('DELETE FROM video_records WHERE id = %s', (video_id,))
print(f'   已删除 pending={pending_id} video={video_id}')
if '/uploads/' in url:
    rel = url.split('/uploads/', 1)[1]
    folder = os.path.join(ROOT, 'uploads', rel.split('/')[0])
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
        print(f'   已删除目录 {os.path.basename(folder)}')

print('\n结果:', '✅ 通过' if ok else '❌ 失败')
sys.exit(0 if ok else 1)
