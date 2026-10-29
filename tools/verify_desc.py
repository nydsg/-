"""验证简介字段端到端落地，并清理测试数据。"""
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
s.post(f'{BASE}/api/auth/login',
       json={'username': 'demo', 'password': '123456'}, timeout=10)

# 1. 字段存在性
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import use_cursor

with use_cursor() as (_c, cur):
    cur.execute("""
        SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'video_records'
          AND COLUMN_NAME = 'zuopin_desc'
    """)
    row = cur.fetchone()
print('1. 字段 video_records.zuopin_desc:', row or '❌ 缺失')

# 2. 按简介关键字搜索
r = s.get(f'{BASE}/api/videos', params={'q': '端到端落地'}, timeout=10).json()
print(f"2. 搜简介关键字命中 total={r.get('total')} "
      f"→ {[i['title'] for i in r.get('items', [])]}")

# 3. 列表接口是否带 desc
items = r.get('items') or []
if items:
    print(f"3. 列表返回 desc 字段: {repr(items[0].get('desc'))[:60]}")

# 4. 清理测试视频（只删标题为「简介字段验证」的）
with use_cursor() as (_c, cur):
    cur.execute("SELECT id, zuopin_name, zuopin_path FROM video_records "
                "WHERE zuopin_name = '简介字段验证'")
    rows = cur.fetchall()
print(f'4. 待清理测试作品: {[(r["id"], r["zuopin_name"]) for r in rows]}')
for r in rows:
    with use_cursor(commit=True) as (_c, cur):
        cur.execute('DELETE FROM video_records WHERE id = %s', (r['id'],))
    # 删掉磁盘文件
    try:
        p = r.get('zuopin_path') or ''
        folder = os.path.dirname(p)
        if folder and os.path.isdir(folder):
            import shutil
            shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        pass
    print(f"   已删除 id={r['id']}")

# 5. 清理后总数
b = s.get(f'{BASE}/api/videos', params={'size': 50}, timeout=10).json()
print(f"5. 清理后作品总数: {b.get('total')}")
