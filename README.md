# 问序｜在线问卷系统

问序是基于 MongoDB、FastAPI 和 React 的在线问卷系统，支持创建问卷、条件跳转、答案校验、发布分享、登录填写和结果统计。本仓库包含数据库课程第一阶段的代码、设计说明和测试材料，项目完成报告单独提交。

## 一、启动前准备

以下步骤适用于 **Windows PowerShell**，需要先安装：

- **Python 3.12 或 3.13（64 位）**。
- **MongoDB Server 8.x**，需要其中的 `mongod.exe`；仅安装 Compass 不够。
- 常用浏览器。

已附带前端构建文件，**直接体验不需要 Node.js、npm 或 mongosh**。首次安装 Python 依赖需要联网。请保留完整目录结构，不要只复制 `backend`。

## 二、首次安装与启动

### 1. 创建环境并生成配置

在**本 README 所在的项目根目录**打开 PowerShell，然后执行：

```powershell
Set-Location ".\01_代码"
py -3.13 -m venv ".\backend\.venv"
& ".\backend\.venv\Scripts\python.exe" -m pip install -r ".\backend\requirements-lock.txt"
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\setup_env.py"
```

安装的是 Python 3.12 时，将第二行的 `py -3.13` 改为 `py -3.12`；没有 `py` 启动器时可使用对应版本的 `python`。脚本会自动生成本地 `.env` 和随机密钥，已有配置不会被覆盖，无需手动填写密钥或激活虚拟环境。

**后续命令均在 `01_代码` 目录执行。** 首次配置只做一次；以后启动时也先进入 `01_代码` 目录，再从下面的数据库启动步骤开始。

### 2. 启动 MongoDB

```powershell
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\dev_mongo.py" start
```

如果提示找不到 `mongod`，用下面这条替代，并将引号内路径改为本机 `mongod.exe` 的实际位置：

```powershell
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\dev_mongo.py" start --mongod "C:\Program Files\MongoDB\Server\8.0\bin\mongod.exe"
```

显示 `Started` 或 `Already running` 后继续。脚本会在本机 **27018** 端口启动并初始化 `rs0` 单节点副本集，后端默认使用 `survey_system` 数据库，无需另建集合或导入示例数据。不能用普通 standalone 实例代替副本集。

### 3. 启动网页与后端

在同一个终端执行：

```powershell
& ".\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --no-access-log
```

看到 `Application startup complete` 后，保持终端打开，在浏览器访问：

- **[问序首页](http://127.0.0.1:8000/)**：项目体验入口。
- [健康检查](http://127.0.0.1:8000/health)：正常返回 `{"status":"ok"}`。
- [Swagger 接口文档](http://127.0.0.1:8000/docs)：用于查看和调试接口。

终端持续运行、不返回输入提示是正常现象。后端同时提供前端页面，无需再启动其他网页服务。

## 三、建议体验流程

1. **注册并登录**：没有预置账号或问卷，首次进入空白工作空间是正常的。
2. **创建问卷**：填写问卷标题，添加单选题和数字题，填写题干、选项及需要的输入限制。若想多次体验同一问卷，请在发布前开启“允许同一账号多次填写”。
3. **保存并发布**：保存草稿，点击“发布问卷”并确认。发布后题目、选项和跳转规则不能再修改。
4. **填写并提交**：复制分享链接，在浏览器打开，点击“开始填写”，逐题回答后点击“确认提交”。创建者可以填写自己的问卷，无需另注册账号；匿名问卷也需要登录。
5. **查看结果**：返回“我的问卷”，进入“查看统计”，检查答卷、选项计数和数字均值。如需体验条件跳转，可另建一份问卷，在发布前配置向后跳转规则，再发布和填写。

未提交的填写答案不自动保存，刷新或离开页面后不能恢复。

## 四、停止与常见问题

在运行后端的终端按 `Ctrl+C`，再执行以下命令停止本项目数据库：

```powershell
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\dev_mongo.py" stop
```

数据库文件保留，下次启动后仍可访问原数据。

| 问题 | 处理 |
|---|---|
| 找不到脚本、模块或 `python.exe` | 确认已经进入 `01_代码`，并完成首次安装；使用上面的完整命令 |
| 8000 端口被占用 | 先确认是否已启动项目；若为其他服务，将后端命令改为 `--port 8001`，访问地址也改为 8001 |
| 27018 端口被其他实例占用 | 按 [数据库端口冲突说明](01_代码/README.md#数据库端口冲突) 同步调整 `.env` 和管理脚本参数 |
| 数据库连接失败 | 先启动数据库，执行 `dev_mongo.py status` 检查状态，并核对 `.env` 中的端口和副本集名；完整命令见 [运行说明](01_代码/README.md#查看状态与停止) |

## 五、项目材料索引

| 入口 | 内容 |
|---|---|
| [01_代码](01_代码/README.md) | 代码、详细运行说明；附后端配置与前端开发说明 |
| [02_文档](02_文档/) | 系统说明、数据库设计、API、关键逻辑和 AI 使用日志 |
| [03_测试](03_测试/README.md) | 六类课程测试、执行结果、原始证据和复现方法 |

仓库不包含真实 `.env`、虚拟环境、依赖目录和运行数据，运行后生成的这些文件已通过 `.gitignore` 排除。
