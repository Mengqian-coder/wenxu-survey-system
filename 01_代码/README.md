# 问序｜安装与运行

本目录包含 `backend` 后端、`database` 数据库定义和 `frontend` 前端源码及构建文件。保留三个目录的同级关系；后端会读取相邻的数据库定义并托管 `frontend/dist`。

## 环境要求

| 软件 | 要求 |
|---|---|
| Python | 推荐 64 位 3.12 或 3.13；已分别用于自动化测试和启动检查 |
| MongoDB Server | 推荐 8.x，已在 8.3.11 验证；必须使用支持事务的副本集 |
| Node.js | 前端开发、前端测试或同步 Schema 定义时需要，推荐 22.12+ 或 24 |
| 浏览器 | 现代浏览器；浏览器自动化测试使用 Microsoft Edge |

以下命令均在 **`01_代码` 目录的 Windows PowerShell** 中执行。需提前安装 MongoDB Server；依赖安装需要访问 Python/npm 软件源。提交副本已附前端构建文件，只体验现有页面无需安装 Node.js。

## 首次配置

```powershell
py -3.13 -m venv ".\backend\.venv"
& ".\backend\.venv\Scripts\python.exe" -m pip install -r ".\backend\requirements-lock.txt"
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\setup_env.py"
```

使用 Python 3.12 时将首条改为 `py -3.12`；没有 `py` 时可用已安装的 `python`。后续命令直接调用虚拟环境中的 Python，无需激活环境。

配置脚本从模板生成 `backend/.env` 和随机签名密钥，不输出密钥、不覆盖已有配置。默认连接 `mongodb://127.0.0.1:27018/?replicaSet=rs0`，数据库为 `survey_system`；其他配置见 [后端说明](backend/README.md#配置)。需要调整时修改本地 `.env`。

## 启动与访问

先启动数据库，再启动后端；以后日常使用直接执行本节即可。

```powershell
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\dev_mongo.py" start
& ".\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

若 `mongod` 不在 PATH，在数据库命令后添加 `--mongod "C:\实际安装目录\bin\mongod.exe"`，替换为本机真实路径。脚本自动初始化本机单节点 `rs0` 副本集，数据和日志保存在 `output/dev-mongo/27018/`，仅供本机课程运行。

后端默认自动应用集合验证器与索引。看到 `Application startup complete` 后保持终端运行，并打开：

- [问序首页](http://127.0.0.1:8000/)：首次使用自行注册账号，无预置账号或答卷。
- [API 调试页面](http://127.0.0.1:8000/docs)。
- [健康检查](http://127.0.0.1:8000/health)：正常返回 `{"status":"ok"}`。

完整使用流程见 [系统说明](../02_文档/01_系统说明.md)。后端端口被占用时，可将命令中的 `--port 8000` 改为 `--port 8001`，访问地址相应调整。

### 数据库端口冲突

如果 27018 已有其他实例，可连接已知副本集并使用独立数据库，或为本副本改用 27019：将 `.env` 中 `MONGODB_URI` 改为 `mongodb://127.0.0.1:27019/?replicaSet=rs0`，并在数据库 `start`、`status`、`stop` 命令后均加 `--port 27019`。管理脚本只管理本目录记录的实例。

## 查看状态与停止

另开终端查看数据库状态；停止后端时在其终端按 `Ctrl+C`，再停止本项目数据库：

```powershell
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\dev_mongo.py" status
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\dev_mongo.py" stop
```

停止后数据库文件保留，下次启动可继续使用。真实 `.env`、`.venv`、依赖目录及 `output` 均为本机生成文件，再次提交时应排除。

## 常见问题

| 现象 | 处理 |
|---|---|
| 找不到脚本、模块或 `python.exe` | 确认当前目录为 `01_代码`，已完成首次配置，并使用上述完整命令 |
| `mongod not found` | 为启动命令指定正确的 `--mongod` 路径 |
| 数据库连接超时或提示事务不支持 | 用 `status` 检查 `primary=True`，核对 `.env` 端口和副本集；不能使用 standalone 实例 |
| `SECRET_KEY` 缺失或无效 | 首次运行配置脚本；已有 `.env` 时检查密钥是否仍为模板占位值 |
| 首页没有完整界面 | 确认保留 `frontend/dist`；首次重新构建后重启后端 |

前端开发与构建见 [前端 README](frontend/README.md)，后端配置与 Schema 维护见 [后端 README](backend/README.md)，统一测试命令及已提交的执行证据见 [测试说明](../03_测试/README.md)。
