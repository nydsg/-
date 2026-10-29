"""本地运维小工具：查看库表状态 / 灌入演示数据。

用法：
    .venv\\Scripts\\python.exe tools\\db_tool.py status
    .venv\\Scripts\\python.exe tools\\db_tool.py seed
    .venv\\Scripts\\python.exe tools\\db_tool.py reset
"""
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymysql  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402


def connect():
    pwd = ''
    for name in ('.db_password',):
        try:
            with open(os.path.join(ROOT, name), encoding='utf-8') as f:
                pwd = f.read().strip()
        except OSError:
            pass
    pwd = os.environ.get('DB_PASSWORD') or pwd
    return pymysql.connect(
        host=os.environ.get('DB_HOST', 'localhost'),
        user=os.environ.get('DB_USER', 'root'),
        password=pwd,
        port=int(os.environ.get('DB_PORT', 3306)),
        database=os.environ.get('DB_NAME', 'userdb'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def status():
    conn = connect()
    cur = conn.cursor()
    cur.execute('SHOW TABLES')
    key = 'Tables_in_' + os.environ.get('DB_NAME', 'userdb')
    tables = sorted(r[key] for r in cur.fetchall())
    print(f'库 {os.environ.get("DB_NAME", "userdb")} 共 {len(tables)} 张表')
    for t in tables:
        cur.execute(f'SELECT COUNT(*) AS c FROM `{t}`')
        print(f'  {t:<20} {cur.fetchone()["c"]:>5} 行')
    cur.close()
    conn.close()


SEED_USERS = [
    ('demo', '同学小演', '计算机学院', '记录校园里的每一件小事 ✨'),
    ('lin', '林同学', '新闻传播学院', '喜欢拍食堂和晚霞'),
    ('chen', '陈同学', '艺术学院', '舞蹈社 / 摄影'),
]

SEED_VIDEOS = [
    ('图书馆的深夜', '校园日常,学习', 1280, 'lin'),
    ('食堂阿姨的一天', '校园日常,人物', 960, 'chen'),
    ('社团纳新现场', '社团活动,青春', 2140, 'demo'),
    ('毕业季的操场', '毕业季,校园日常', 3320, 'lin'),
    ('实验室安全演示', '学习,实验室', 640, 'chen'),
    ('元旦晚会彩排', '文艺汇演,社团活动', 1750, 'demo'),
]


def seed():
    conn = connect()
    cur = conn.cursor()

    # 用户
    ids = {}
    for username, name, school, sig in SEED_USERS:
        cur.execute('SELECT id FROM users WHERE username = %s', (username,))
        row = cur.fetchone()
        if row:
            ids[username] = row['id']
            continue
        cur.execute(
            'INSERT INTO users (username, password, email, name, school, signature) '
            'VALUES (%s,%s,%s,%s,%s,%s)',
            (username, generate_password_hash('123456'), f'{username}@campus.edu',
             name, school, sig))
        ids[username] = cur.lastrowid
    conn.commit()
    print('用户:', ids)

    # 作品（用原项目自带的静态图当封面）
    covers = ['/static/jpg/home.jpg', '/static/jpg/shouye.jpg', '/static/jpg/dongtai.jpg',
              '/static/jpg/beijing.jpg', '/static/jpg/chuangzuo.jpg', '/static/jpg/hot.jpg']
    for i, (title, tags, views, owner) in enumerate(SEED_VIDEOS):
        cur.execute('SELECT id FROM video_records WHERE zuopin_name = %s', (title,))
        if cur.fetchone():
            continue
        cur.execute(
            """INSERT INTO video_records
                 (user_id, username, zuopin_name, saved_name, zuopin_path, zuopin_url,
                  cover_path, file_size, upload_time, view_count, tags, audit_result)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s, NOW(), %s,%s,'visible')""",
            (ids[owner], owner, title, f'{title}.mp4',
             os.path.join(ROOT, 'uploads', title, f'{title}.mp4'),
             '', covers[i % len(covers)], 1024 * (i + 3), views, tags))
    conn.commit()

    # 把 demo 设为审核员
    cur.execute('INSERT IGNORE INTO auditor (user_id) VALUES (%s)', (ids['demo'],))
    conn.commit()

    # 判例库（让 RAG 有"历史判例"可检索）
    cases = [
        ('成人礼视频误判申诉', '学生拍摄成人礼仪式，曾被判低俗，人工复核为正常校园仪式',
         '色情低俗,礼仪豁免', 'pass'),
        ('兼职日结广告', '视频结尾出现"加微信日结"二维码，判定为站外引流',
         '广告营销', 'reject'),
        ('化学实验明火演示', '实验室加热演示，有防护说明，判定合规',
         '暴力危险,实验室', 'pass'),
    ]
    for title, content, labels, verdict in cases:
        cur.execute('SELECT id FROM audit_cases WHERE title = %s', (title,))
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO audit_cases (title, content, labels, verdict, source) '
                "VALUES (%s,%s,%s,%s,'manual')", (title, content, labels, verdict))
    conn.commit()

    cur.close()
    conn.close()
    print('演示数据已就绪')


def reset():
    conn = connect()
    cur = conn.cursor()
    cur.execute('SET FOREIGN_KEY_CHECKS = 0')
    cur.execute('SHOW TABLES')
    key = 'Tables_in_' + os.environ.get('DB_NAME', 'userdb')
    for r in cur.fetchall():
        cur.execute(f'TRUNCATE TABLE `{r[key]}`')
    cur.execute('SET FOREIGN_KEY_CHECKS = 1')
    conn.commit()
    cur.close()
    conn.close()
    print('已清空所有表数据（结构保留）')


if __name__ == '__main__':
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    {'status': status, 'seed': seed, 'reset': reset}.get(action, status)()
