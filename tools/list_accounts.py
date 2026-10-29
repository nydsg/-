"""查看当前库里的账号（只读，不打印密码哈希）。"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pwd = ''
try:
    with open(os.path.join(ROOT, '.db_password'), encoding='utf-8') as f:
        pwd = f.read().strip()
except OSError:
    pass
pwd = os.environ.get('DB_PASSWORD') or pwd

conn = pymysql.connect(host='localhost', user='root', password=pwd, port=3306,
                       database='userdb', charset='utf8mb4',
                       cursorclass=pymysql.cursors.DictCursor)
cur = conn.cursor()

cur.execute("""
    SELECT u.id, u.username, u.name, u.school, u.email, u.created_at,
           (SELECT COUNT(*) FROM audience a WHERE 1=0) AS _x
    FROM users u ORDER BY u.id
""") if False else None

cur.execute("""
    SELECT u.id, u.username, u.name, u.school, u.email, u.created_at,
           EXISTS(SELECT 1 FROM auditor au WHERE au.user_id = u.id) AS is_auditor,
           (SELECT COUNT(*) FROM video_records v WHERE v.user_id = u.id) AS works
    FROM users u ORDER BY u.id
""")
rows = cur.fetchall()

print('当前库 userdb 中的账号：')
print(f'{"id":<4}{"用户名":<10}{"昵称":<10}{"学院":<16}{"审核员":<8}{"作品数":<6}')
for r in rows:
    print(f'{r["id"]:<4}{r["username"]:<10}{r["name"]:<10}'
          f'{(r["school"] or "-"):<16}{"是" if r["is_auditor"] else "否":<8}{r["works"]:<6}')

print()
cur.execute('SELECT COUNT(*) AS c FROM video_records')
print('作品总数:', cur.fetchone()['c'])
cur.execute('SELECT COUNT(*) AS c FROM audit_cases')
print('判例库:', cur.fetchone()['c'], '条')

# 验证密码是否统一为 123456（用 werkzeug 校验，不输出哈希）
from werkzeug.security import check_password_hash
print()
print('口令校验（测试 123456）：')
cur.execute('SELECT username, password FROM users ORDER BY id')
for r in cur.fetchall():
    ok = check_password_hash(r['password'], '123456')
    print(f'  {r["username"]:<10} {"✔ 匹配 123456" if ok else "✘ 不是 123456"}')

cur.close()
conn.close()
