# 运行说明（RUN.md）

本项目是 Flask + MySQL 的校园视频社区。本文只回答一个问题：**怎么把它在本机跑起来**。

## 1. 环境要求

| 组件 | 要求 | 备注 |
| --- | --- | --- |
| Python | **3.11**（3.10 未验证） | 项目 `.venv` 在 3.11.9 上验证通过 |
| MySQL | **8.0+** | 库与表统一使用 utf8mb4 |
| Git | 可选 | 只有安装 `requirements-ai.txt` 里的 CLIP 时需要 |
| 操作系统 | Windows / macOS / Linux | 示例命令以 Windows PowerShell 为主 |

## 2. 首次运行（5 步）

### 第 1 步：创建虚拟环境

```powershell
cd C:\Users\联想\Desktop\xiangmu1
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS / Linux 用 `source .venv/bin/activate`。激活后提示符前会出现 `(.venv)`。

### 第 2 步：安装主干依赖

```powershell
python -m pip install -U pip
pip install -r requirements.txt
```

`requirements.txt` 只含跑网站必需的包（Flask、Flask-SQLAlchemy、PyMySQL、OpenCV、numpy、Pillow），
已剔除 `clip @ file:///E:/...` 这类绝对路径依赖和地理绘图类无关包，可一次装完。

### 第 3 步：创建数据库

```sql
CREATE DATABASE userdb CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 第 4 步：配置数据库口令

二选一：

- 环境变量（推荐）：`$env:DB_PASSWORD = "你的MySQL口令"`
- 文件：在项目根目录新建 `.db_password`，**只写口令本身**（UTF-8 无 BOM、无引号、无换行），该文件已被 `.gitignore` 忽略。

MySQL 用户名默认 `root`、库名默认 `userdb`；不同就设 `DB_USER` / `DB_NAME`。

### 第 5 步：启动

```powershell
python zhanshizhan.py
```

浏览器打开 <http://127.0.0.1:5000/index>。控制台打印「服务启动成功」即表示启动完成。

## 3. 可选：启用 AI 审核

**不装 AI 依赖，网站照样能跑**，只是 AI 审核通道降级：

- `/upload` 等走 AI 审核的投稿接口会返回「AI 审核模块不可用」，提示改走人工审核；
- 首页、播放、收藏、评论、搜索等主干功能不受影响。

想真正启用 AI 审核，再执行（CPU 版 torch 先单独装更快）：

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ai.txt
```

注意两点：`requirements-ai.txt` 里的 `git+https://github.com/openai/CLIP.git` 需要本机有 Git；
首次审核时 CLIP 会联网下载 ViT-B/32 权重（约 350MB），离线环境请提前准备好缓存。

## 4. 环境变量

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `SECRET_KEY` | 自动生成 | Flask 会话签名密钥；不设置时自动生成并写入 `.secret_key` |
| `DB_USER` | `root` | MySQL 用户名 |
| `DB_PASSWORD` | 空 | MySQL 口令，优先级高于 `.db_password` 文件 |
| `DB_NAME` | `userdb` | 数据库名 |
| `DB_HOST` | `localhost` | 数据库地址 |
| `DB_PORT` | `3306` | 数据库端口，填错时自动回退到 3306 |
| `ALLOW_SELF_AUDITOR` | `0` | 设为 `1` 时注册满 30 天的用户可自助成为审核员；默认只登记申请、需管理员审批 |
| `ENABLE_STREAM_API` | `0` | 设为 `1` 才开放视频流接口（仅审核员可调用） |
| `SESSION_COOKIE_SECURE` | `0` | 设为 `1` 时 Cookie 只在 HTTPS 链路发送，网站启用 HTTPS 后再打开 |
| `PUBLIC_BASE_URL` | 空 | 生成媒体 URL 的前缀。留空时返回 `/uploads/...` 相对路径，任何域名/端口都能访问；要绝对地址（CDN）才设 |
| `HOST` | `127.0.0.1` | 监听地址；局域网访问设 `0.0.0.0` |
| `PORT` | `5000` | 监听端口 |
| `DEBUG` | `1` | 设为 `0` 关闭调试模式（生产必须关掉） |

## 4.1 健康检查

服务起来后访问 <http://127.0.0.1:5000/healthz>，返回 JSON 说明各层状态：

```json
{"status": "ok", "db": true, "ai_auditor": false, "recommender": true,
 "base_dir": "...", "upload_dir": "..."}
```

`db` 为 `false` 时 HTTP 状态码是 503 —— 通常意味着口令/库名不对，或 MySQL 没启动。
`ai_auditor` 为 `false` 表示没装 AI 依赖，属正常降级。

## 5. 数据表与审核员

首次启动时 `create_users_table()` 与 `db.create_all()` 会自动建表，无需手动导入 SQL。
共 13 张业务表 + 1 张审核员申请表：

`users`、`lishi`、`video_records`、`dianzan`、`shoucang`、`comments`、
`notifications`、`NOAI_shenhe`、`user_dynamics`、`auditor`、`follows`、
`folder_backgrounds`、`fav_backgrounds`（收藏夹背景图库）、`auditor_apply`（审核员申请）。

> 其中 `fav_backgrounds` 表和 `users.fav_bg_url` 列在原项目里**从未写进建表脚本**
> （是在某台机器的数据库上手工加的），现已补进 `create_users_table()`：
> 缺表时自动 `CREATE TABLE`，缺列时自动 `ALTER TABLE ADD COLUMN`（幂等，可反复执行）。

**如果你有旧数据库**：老记录的 `zuopin_url` / `cover_path` 里可能存着
`http://localhost:5000/...` 的绝对地址，换端口或换机后会失效。用这条 SQL 一次性改成相对路径：

```sql
UPDATE video_records SET zuopin_url  = REPLACE(zuopin_url,  'http://localhost:5000', '')
 WHERE zuopin_url  LIKE 'http://localhost:5000/%';
UPDATE video_records SET cover_path  = REPLACE(cover_path,  'http://localhost:5000', '')
 WHERE cover_path  LIKE 'http://localhost:5000/%';
```

把某个用户设为审核员（先查 id）：

```sql
SELECT id, username FROM users ORDER BY id;
INSERT INTO auditor (user_id) VALUES (2);          -- 2 换成上一步查到的 id
UPDATE auditor_apply SET status = 'approved' WHERE user_id = 2;  -- 顺便审批其申请表
```

## 6. 常见问题

### `ModuleNotFoundError: No module named 'pymysql'`

依赖没装，或命令打到了另一个 Python 解释器。确认虚拟环境已激活（有 `(.venv)` 前缀），
再执行 `pip install -r requirements.txt`。

### `Access denied for user 'root'@'localhost'`

口令不对或该用户没有权限。核对 `.db_password` / `DB_PASSWORD`：只写口令本身，不要带引号、空格和换行。
可用 `mysql -u root -p` 手动登录，确认口令能进。

### `Can't connect to MySQL server on 'localhost'`

MySQL 服务没启动，或端口不对。Windows 在「服务」里启动 `MySQL80`（名字以本机为准）；
改过端口就设 `DB_PORT`，并确认它是纯数字（写错会自动回退到 3306，反而更容易误判）。

### 页面 500 怎么定位

项目以 `debug=True` 运行，**控制台会打印完整 traceback**，看最后一行即可定位。
接口层的错误还会被 `logger.error` 记录。常见两类：`TemplateNotFound`（模板没放对，
应在 `templates\` 下且文件名拼写一致）、`OperationalError`（数据库连接问题，回到上两条排查）。

### 上传大文件失败

`MAX_CONTENT_LENGTH` 限制为 500MB，超过会被 Flask 直接拒绝，属于预期行为。
