# 贴吧自动签到

一个基于 Python 标准库 HTTP 服务实现的百度贴吧自动签到网站。项目支持多用户注册登录、添加百度账号 `BDUSS`、同步关注贴吧、确认签到贴吧、手动签到、服务器定时自动签到，以及管理员后台管理。

当前版本：`v1.0.1`

## 截图
<img width="2552" height="1308" alt="image-20260608093525812" src="https://github.com/user-attachments/assets/af02466a-e5d3-4337-b559-b2aef491491a" />

<img width="2552" height="1308" alt="image-20260608093654304" src="https://github.com/user-attachments/assets/01bd5b88-8924-4807-91b5-22f004eba818" />

<img width="2552" height="1308" alt="image-20260608093820830" src="https://github.com/user-attachments/assets/8bc1e1c4-27ad-4a36-9a4d-dc495c5f3d0e" />

## 功能特性

### 用户功能

- 用户注册、登录、退出
- 用户修改登录密码
- 第一个注册用户自动成为管理员
- 添加百度账号备注和 `BDUSS`
- 自动同步该百度账号关注的贴吧
- 用户确认需要签到的贴吧后才会执行签到
- 手动触发单个百度账号签到
- 删除已添加的百度账号
- 查看账号状态、同步时间、签到时间和签到记录

### 自动签到

- 后台调度器每 300 秒检查一次
- 每天 `06:00` 到 `23:59` 之间自动执行
- 每个百度账号每天只会自动处理一次
- 已成功签到或已因风控暂停的账号当天不会重复签到
- 单个贴吧之间按环境变量设置随机延迟
- 不同账号之间额外随机间隔 3 到 8 秒

### 管理员功能

管理员可进入 `/admin` 后台，查看和管理：

- 今日签到任务统计
- 今日成功、失败、暂停数量
- 当前程序版本号
- 所有用户列表
- 所有百度账号列表
- 审计日志
- 封禁 / 恢复普通用户
- 删除普通用户
- 修改普通用户密码
- 暂停 / 恢复百度账号

### 安全设计

- 不保存百度账号明文密码
- 只使用用户自行填写的 `BDUSS`
- `BDUSS` 使用 `AES-GCM` 加密保存
- 登录密码使用 `PBKDF2-HMAC-SHA256` 加盐哈希保存
- Session 使用随机 token，有效期 14 天
- 管理员不能封禁或删除当前登录的管理员自己
- 遇到验证码、风控或异常登录时，不绕过验证，只暂停账号等待用户处理

## 技术栈

- Python 3
- SQLite
- `requests`
- `cryptography`
- Tailwind CSS CDN
- Iconify CDN

项目没有使用 Flask、Django 等 Web 框架，HTTP 服务由 Python 标准库 `http.server` 提供。

## 快速运行

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

启动服务：

```bash
python3 app.py
```

默认访问地址：

```txt
http://127.0.0.1:8000
```

## 环境变量

可通过环境变量调整运行配置：

| 变量             | 默认值      | 说明                        |
| ---------------- | ----------- | --------------------------- |
| `HOST`           | `127.0.0.1` | 服务监听地址                |
| `PORT`           | `8000`      | 服务监听端口                |
| `APP_SECRET`     | 空          | 用于加密 `BDUSS` 的固定密钥 |
| `SIGN_DELAY_MIN` | `0.2`       | 单个贴吧签到最小间隔秒数    |
| `SIGN_DELAY_MAX` | `0.8`       | 单个贴吧签到最大间隔秒数    |

示例：

```bash
HOST=0.0.0.0 PORT=8000 APP_SECRET='change-me' SIGN_DELAY_MIN=0.2 SIGN_DELAY_MAX=0.8 python3 app.py
```

生产环境必须固定设置 `APP_SECRET`。如果未设置，程序会自动生成 `.app_secret` 文件作为本地密钥。

## 数据文件

程序运行目录下会产生：

```txt
tieba.db
tieba.db-shm
tieba.db-wal
.app_secret
```

说明：

- `tieba.db`：SQLite 主数据库
- `tieba.db-shm` / `tieba.db-wal`：SQLite WAL 模式文件
- `.app_secret`：未设置 `APP_SECRET` 时自动生成的本地加密密钥

注意：

- 不要公开这些文件
- 不要提交到公开仓库
- 备份数据库时应同时备份密钥
- 如果丢失 `APP_SECRET` 或 `.app_secret`，已保存的 `BDUSS` 将无法解密

## 使用流程

1. 启动网站
2. 注册第一个用户，第一个用户会自动成为管理员
3. 登录后进入控制台
4. 添加百度账号备注和 `BDUSS`
5. 系统自动同步关注贴吧
6. 在贴吧确认页面勾选需要签到的贴吧
7. 返回控制台后可手动签到
8. 后续系统会在每日自动签到时间段内定时签到

## 获取 BDUSS

`BDUSS` 是百度登录 Cookie 中的一项。需要用户自行从浏览器 Cookie 中获取并填写。

请注意：

- `BDUSS` 等同于登录凭据，应妥善保管
- 不要把 `BDUSS` 发给不可信的人
- 如果担心泄露，可在百度账号中退出登录或修改密码使其失效

## 1Panel / Docker 部署提示

如果使用 Docker Compose 部署，并且 Dockerfile 中使用：

```dockerfile
COPY app.py /app/app.py
```

那么更新 `app.py` 后，不能只重启容器，必须重新构建镜像。

推荐更新流程：

1. 备份数据库和密钥
2. 替换服务器上的 `app.py`
3. 重新构建镜像 / 编排
4. 重启容器
5. 登录管理员后台查看版本号是否更新

命令示例：

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

管理员后台会显示当前运行版本，可用于确认服务器是否已经运行最新脚本。

## 备份建议

建议定期备份：

```txt
tieba.db
.app_secret
.env
```

如果使用 1Panel 部署，推荐备份：

```txt
/opt/tieba-sign/data/tieba.db
/opt/tieba-sign/data/.app_secret
/opt/tieba-sign/.env
```

恢复时必须保证数据库和密钥匹配，否则数据库中的 `BDUSS` 无法解密。

## 安全边界

本项目只做自动化签到，不提供也不会尝试：

- 百度账号密码登录
- 验证码识别
- 风控绕过
- 异常登录处理
- 批量撞库或账号测试

当百度返回验证码、风控、账号异常或 `BDUSS` 失效时，系统会暂停相关账号，需要用户自行处理后再恢复。
