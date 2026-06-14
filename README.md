# 贴吧自动签到网站

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

可用环境变量：

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

## 1Panel / Docker 详细部署

推荐使用 1Panel 的「容器编排」功能，通过 Docker Compose 部署。这样可以固定 Python 运行环境，并把数据库持久化到服务器目录中。

### 1. 准备服务器目录

建议在服务器创建项目目录：

```bash
mkdir -p /opt/tieba-sign/data
cd /opt/tieba-sign
```

最终目录建议如下：

```txt
/opt/tieba-sign
├── app.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env
└── data/
    ├── tieba.db
    └── .app_secret
```

说明：

- `app.py`：主程序
- `requirements.txt`：Python 依赖
- `Dockerfile`：镜像构建文件
- `docker-compose.yml`：容器编排文件
- `.env`：生产环境变量
- `data/`：数据库和密钥持久化目录

### 2. 上传项目文件

将本项目中的以下文件上传到服务器：

```txt
/opt/tieba-sign/app.py
/opt/tieba-sign/requirements.txt
```

如果服务器还没有 Dockerfile 和 docker-compose.yml，需要继续创建下面两个文件。

### 3. 创建 Dockerfile

在 `/opt/tieba-sign/Dockerfile` 写入：

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py

EXPOSE 8000

CMD ["python", "app.py"]
```

注意这里使用了：

```dockerfile
COPY app.py /app/app.py
```

这表示 `app.py` 会在构建镜像时复制进容器。以后更新 `app.py` 后，必须重新构建镜像，不能只重启容器。

### 4. 创建 docker-compose.yml

在 `/opt/tieba-sign/docker-compose.yml` 写入：

```yaml
services:
  tieba-sign:
    build: .
    container_name: tieba-sign
    restart: unless-stopped
    env_file:
      - .env
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./data/tieba.db:/app/tieba.db
      - ./data/.app_secret:/app/.app_secret
    environment:
      - TZ=Asia/Shanghai
```

端口说明：

```txt
127.0.0.1:8000:8000
```

表示容器的 `8000` 端口只绑定到服务器本机，不直接暴露到公网。公网访问建议交给 1Panel 网站反向代理。

### 5. 创建 .env

在 `/opt/tieba-sign/.env` 写入：

```env
HOST=0.0.0.0
PORT=8000
APP_SECRET=请替换为一段足够长的随机密钥
SIGN_DELAY_MIN=0.2
SIGN_DELAY_MAX=0.8
TZ=Asia/Shanghai
```

生成随机密钥可以执行：

```bash
openssl rand -hex 32
```

然后把输出内容填到 `APP_SECRET=` 后面。

重要说明：

- `APP_SECRET` 必须固定
- `APP_SECRET` 用于加密数据库中的 `BDUSS`
- 如果后续更换 `APP_SECRET`，旧数据库里的 `BDUSS` 将无法解密
- `.env` 不要公开，不要提交到公开仓库

### 6. 初始化持久化文件

在 `/opt/tieba-sign` 下执行：

```bash
touch data/tieba.db
touch data/.app_secret
```

如果你已经有旧数据库，需要把旧文件放到：

```txt
/opt/tieba-sign/data/tieba.db
```

如果旧环境没有设置 `APP_SECRET`，而是使用自动生成的 `.app_secret`，也要一起迁移：

```txt
/opt/tieba-sign/data/.app_secret
```

### 7. 在 1Panel 中创建容器编排

在 1Panel 后台操作：

1. 打开「容器」
2. 进入「编排」
3. 点击「创建编排」或「新建编排」
4. 选择或填写 `/opt/tieba-sign/docker-compose.yml`
5. 启动编排
6. 等待镜像构建完成
7. 确认容器 `tieba-sign` 状态为运行中

启动成功后，容器日志中应能看到类似：

```txt
Tieba sign-in web is running: http://0.0.0.0:8000
```

### 8. 配置 1Panel 网站反向代理

因为容器端口只绑定到服务器本机，所以需要通过 1Panel 网站反向代理访问。

在 1Panel 后台：

1. 打开「网站」
2. 创建网站
3. 类型选择「反向代理」
4. 填写你的域名
5. 代理地址填写：

```txt
http://127.0.0.1:8000
```

6. 保存配置
7. 按需申请并开启 HTTPS 证书

最终访问地址类似：

```txt
https://你的域名
```

### 9. 首次访问和初始化管理员

部署完成后：

1. 打开网站域名
2. 点击注册
3. 注册第一个用户
4. 第一个注册用户会自动成为管理员
5. 登录后进入控制台
6. 管理员可通过顶部导航进入「管理员后台」

管理员后台会显示当前运行版本号，例如：

```txt
当前版本：v1.0.1
```

这个版本号可用于确认服务器是否已经运行最新脚本。

### 10. 更新 app.py

由于 Dockerfile 使用 `COPY app.py /app/app.py`，更新代码后必须重新构建镜像。

推荐更新流程：

1. 备份数据库和密钥
2. 替换服务器上的 `/opt/tieba-sign/app.py`
3. 重新构建编排 / 镜像
4. 重启容器
5. 登录管理员后台确认版本号是否更新

命令方式：

```bash
cd /opt/tieba-sign
docker compose down
docker compose build --no-cache
docker compose up -d
```

如果在 1Panel 中操作，需要选择「重新构建」「重建镜像」「重新构建编排」等操作，而不是只点「重启」。

只重启容器通常不会生效，因为容器仍然会使用旧镜像里的旧 `app.py`。

### 11. 检查当前容器内运行的 app.py

如果你不确定服务器是否已经使用新脚本，可以进入容器检查：

```bash
docker exec -it tieba-sign sh
```

进入容器后查看 `/app/app.py`：

```bash
python - <<'PY'
from pathlib import Path
print(Path('/app/app.py').read_text()[:1000])
PY
```

如果容器里的 `/app/app.py` 不是新内容，说明镜像没有重新构建成功。

### 12. 常见问题排查

#### 网站访问不到

检查容器是否运行：

```bash
docker ps
```

检查容器日志：

```bash
docker logs tieba-sign
```

确认 1Panel 反向代理地址是：

```txt
http://127.0.0.1:8000
```

#### 替换 app.py 后网站没变化

通常原因是只重启了容器，没有重新构建镜像。

解决：

```bash
cd /opt/tieba-sign
docker compose down
docker compose build --no-cache
docker compose up -d
```

然后登录管理员后台查看版本号。

#### 登录后数据丢失

检查数据库是否正确挂载：

```yaml
volumes:
  - ./data/tieba.db:/app/tieba.db
```

如果没有挂载，数据可能保存在容器内部，容器重建后会丢失。

#### BDUSS 无法解密

通常是密钥变了。

需要确认以下文件或变量和旧环境一致：

```txt
APP_SECRET
.app_secret
```

如果旧环境使用 `.app_secret`，迁移数据库时必须一起迁移 `.app_secret`。

#### 自动签到没有执行

检查：

- 服务器时间和时区是否正确
- `.env` 中是否设置 `TZ=Asia/Shanghai`
- 当前时间是否在 `06:00` 到 `23:59` 之间
- 账号状态是否为 `active`
- 贴吧是否已经确认
- 当天是否已经成功或暂停过
- 容器日志是否有异常

#### 端口冲突

如果服务器本机 `8000` 已被占用，可以修改 docker-compose.yml：

```yaml
ports:
  - "127.0.0.1:18000:8000"
```

然后 1Panel 反向代理地址也改成：

```txt
http://127.0.0.1:18000
```

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

- 不保存百度明文密码
- 不绕过验证码
- 不处理异常登录风控
- 账号触发验证码或风控后暂停，等待用户处理
