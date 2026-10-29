"""数据访问层：沿用原生 pymysql（与既有 13 张表零迁移成本）。

设计要点：
- 提供 use_cursor() 上下文管理器，保证 close；
- 数据库不可用时抛 DatabaseUnavailable，由蓝图统一转成 503（不吐 traceback）；
- 所有 SQL 一律参数化。
"""
import logging
from contextlib import contextmanager

import pymysql

from .config import Config

logger = logging.getLogger(__name__)


class DatabaseUnavailable(RuntimeError):
    """数据库连不上（配置错 / 服务未启动 / 口令错）。"""


def _connect(dict_cursor: bool = False):
    conf = dict(Config.DB_CONFIG)
    conf.pop('cursorclass', None)
    if not conf.get('password'):
        raise DatabaseUnavailable(
            '数据库口令未配置：请设置环境变量 DB_PASSWORD 或创建 .db_password 文件')
    try:
        conn = pymysql.connect(
            cursorclass=pymysql.cursors.DictCursor if dict_cursor
            else pymysql.cursors.Cursor,
            **conf,
        )
    except pymysql.MySQLError as e:
        raise DatabaseUnavailable(str(e)) from e
    return conn


@contextmanager
def use_cursor(dict_cursor: bool = True, commit: bool = False):
    """with use_cursor() as (conn, cur): ..."""
    conn = _connect(dict_cursor)
    cur = conn.cursor()
    try:
        yield conn, cur
        if commit:
            conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def ping() -> bool:
    try:
        with use_cursor(dict_cursor=False) as (_conn, cur):
            cur.execute('SELECT 1')
            cur.fetchone()
        return True
    except Exception as e:
        logger.warning('数据库探测失败: %s', e)
        return False


def ensure_schema():
    """建/补全部表结构（幂等，可反复执行）。

    两处关键修复：
    1. **基础表必须先建**：原实现只建 4 张新表，却让它们的外键引用 users；
       空库上会报 1824 Failed to open the referenced table 'users'。
    2. **每条 DDL 独立提交**：原实现把全部语句放在一个事务里，任意一条失败
       就整批回滚，导致"建表失败但服务照常启动"的静默故障。
    """
    # 按外键依赖排序：users → 依赖 users 的表 → 互相独立的新表
    statements: list[tuple[str, str]] = [
        ('users', """CREATE TABLE IF NOT EXISTS users (
               id INT PRIMARY KEY AUTO_INCREMENT,
               username VARCHAR(80) UNIQUE NOT NULL,
               password VARCHAR(200) NOT NULL,
               email VARCHAR(120),
               name VARCHAR(100) NOT NULL,
               school VARCHAR(100) DEFAULT '未公开',
               touxiang_url VARCHAR(500),
               bg_url VARCHAR(500),
               signature VARCHAR(500) DEFAULT '我是超级个性签名qwq......',
               fav_bg_url VARCHAR(500) NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('video_records', """CREATE TABLE IF NOT EXISTS video_records (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT,
               username VARCHAR(80),
               zuopin_name VARCHAR(255),
               saved_name VARCHAR(255),
               zuopin_path VARCHAR(500),
               zuopin_url VARCHAR(500),
               zuopin_desc VARCHAR(1000),
               cover_path VARCHAR(500),
               file_size INT,
               upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               audit_result VARCHAR(50),
               violations VARCHAR(500),
               confidence FLOAT,
               view_count INT DEFAULT 0,
               tags VARCHAR(255),
               KEY idx_upload (upload_time),
               KEY idx_views (view_count),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('lishi', """CREATE TABLE IF NOT EXISTS lishi (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT,
               video_id INT,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               FOREIGN KEY (user_id) REFERENCES users(id),
               FOREIGN KEY (video_id) REFERENCES video_records(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('dianzan', """CREATE TABLE IF NOT EXISTS dianzan (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT,
               video_id INT,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               UNIQUE KEY uniq_like (user_id, video_id),
               FOREIGN KEY (video_id) REFERENCES video_records(id),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('shoucang', """CREATE TABLE IF NOT EXISTS shoucang (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT,
               video_id INT,
               SCname VARCHAR(45),
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               UNIQUE KEY uniq_collect (user_id, video_id, SCname),
               FOREIGN KEY (video_id) REFERENCES video_records(id),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('comments', """CREATE TABLE IF NOT EXISTS comments (
               id INT PRIMARY KEY AUTO_INCREMENT,
               video_id INT NOT NULL,
               user_id INT,
               username VARCHAR(80) NOT NULL,
               name VARCHAR(100) NOT NULL,
               content TEXT NOT NULL,
               parent_id INT DEFAULT 0,
               reply_to_user_id INT DEFAULT NULL,
               reply_to_username VARCHAR(80) DEFAULT NULL,
               reply_to_name VARCHAR(100) DEFAULT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               KEY idx_parent (parent_id),
               FOREIGN KEY (video_id) REFERENCES video_records(id),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('notifications', """CREATE TABLE IF NOT EXISTS notifications (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT NOT NULL,
               type VARCHAR(20) NOT NULL,
               sender_id INT,
               sender_name VARCHAR(100),
               sender_avatar VARCHAR(500),
               video_id INT,
               video_title VARCHAR(255),
               comment_id INT,
               comment_content TEXT,
               reply_content TEXT,
               is_read INT DEFAULT 0,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               KEY idx_read (user_id, is_read),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('NOAI_shenhe', """CREATE TABLE IF NOT EXISTS NOAI_shenhe (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT,
               zuopin_url VARCHAR(500),
               tags VARCHAR(255),
               status VARCHAR(20) DEFAULT 'pending',
               reason VARCHAR(500) DEFAULT '审核已通过',
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               KEY idx_status (status),
               FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('user_dynamics', """CREATE TABLE IF NOT EXISTS user_dynamics (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT NOT NULL,
               content TEXT,
               image_url VARCHAR(500),
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('auditor', """CREATE TABLE IF NOT EXISTS auditor (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT,
               UNIQUE KEY uniq_auditor (user_id),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('follows', """CREATE TABLE IF NOT EXISTS follows (
               id INT PRIMARY KEY AUTO_INCREMENT,
               follower_id INT NOT NULL,
               followed_id INT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               UNIQUE KEY unique_follow (follower_id, followed_id),
               FOREIGN KEY (follower_id) REFERENCES users(id),
               FOREIGN KEY (followed_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('folder_backgrounds', """CREATE TABLE IF NOT EXISTS folder_backgrounds (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT NOT NULL,
               folder_name VARCHAR(100) NOT NULL,
               image_url VARCHAR(500),
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
               UNIQUE KEY unique_folder (user_id, folder_name),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('auditor_apply', """CREATE TABLE IF NOT EXISTS auditor_apply (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT NOT NULL,
               status VARCHAR(20) NOT NULL DEFAULT 'pending',
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               UNIQUE KEY unique_apply (user_id),
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('fav_backgrounds', """CREATE TABLE IF NOT EXISTS fav_backgrounds (
               id INT PRIMARY KEY AUTO_INCREMENT,
               user_id INT NOT NULL,
               image_url VARCHAR(500) NOT NULL,
               is_current TINYINT DEFAULT 0,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               FOREIGN KEY (user_id) REFERENCES users(id)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('audit_runs', """CREATE TABLE IF NOT EXISTS audit_runs (
               id INT PRIMARY KEY AUTO_INCREMENT,
               video_id INT NULL,
               user_id INT NULL,
               filename VARCHAR(255),
               verdict VARCHAR(20),
               score INT,
               confidence FLOAT,
               evidence JSON NULL,
               model VARCHAR(80),
               latency_ms INT,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               KEY idx_created (created_at),
               KEY idx_verdict (verdict)
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
        ('audit_cases', """CREATE TABLE IF NOT EXISTS audit_cases (
               id INT PRIMARY KEY AUTO_INCREMENT,
               title VARCHAR(255),
               content TEXT,
               labels VARCHAR(255),
               verdict VARCHAR(20),
               source VARCHAR(40) DEFAULT 'manual',
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""),
    ]

    created, failed = [], []
    for name, sql in statements:
        # 每条独立连接/独立提交：一条失败不影响其它表
        try:
            with use_cursor(dict_cursor=False, commit=True) as (_conn, cur):
                cur.execute(sql)
            created.append(name)
        except Exception as e:
            failed.append((name, str(e)))
            logger.error('建表失败 %s: %s', name, e)

    # 老库补列：users.fav_bg_url（幂等）
    try:
        with use_cursor(dict_cursor=False, commit=True) as (_conn, cur):
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'users'
                  AND COLUMN_NAME = 'fav_bg_url'
            """)
            row = cur.fetchone()
            if row and row[0] == 0:
                cur.execute('ALTER TABLE users ADD COLUMN fav_bg_url VARCHAR(500) NULL')
                logger.info('已补充字段 users.fav_bg_url')
    except Exception as e:
        failed.append(('users.fav_bg_url', str(e)))
        logger.error('补列失败: %s', e)

    # 老库补列：video_records.zuopin_desc（作品简介，幂等）
    try:
        with use_cursor(dict_cursor=False, commit=True) as (_conn, cur):
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'video_records'
                  AND COLUMN_NAME = 'zuopin_desc'
            """)
            row = cur.fetchone()
            if row and row[0] == 0:
                cur.execute(
                    'ALTER TABLE video_records ADD COLUMN zuopin_desc VARCHAR(1000) NULL')
                logger.info('已补充字段 video_records.zuopin_desc')
    except Exception as e:
        failed.append(('video_records.zuopin_desc', str(e)))
        logger.error('补列失败: %s', e)

    if failed:
        raise RuntimeError(
            f'共 {len(failed)} 项建表/补列失败: ' + '; '.join(f'{n}({e})' for n, e in failed))
    logger.info('表结构就绪，共 %d 张表', len(created))
    return created
