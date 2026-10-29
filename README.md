# 问序｜在线问卷系统

问序是一个基于 MongoDB、FastAPI 和 React 的在线问卷系统，支持题目复用、历史版本、共享、个人题库、问卷填写和统计。以下说明适用于助教在 Windows 电脑上运行项目，所有命令均使用相对路径，项目可以放在任意目录。

## 运行环境

| 软件 | 要求 |
|---|---|
| Windows / PowerShell | 以下命令在 PowerShell 中执行，普通权限即可 |
| Python | 3.12 或 3.13，64 位；下文以 3.13 为例 |
| MongoDB | MongoDB Server 8.x，需要 `mongod.exe`；仅安装 Compass 不够 |
| 浏览器 | Edge、Chrome 等现代浏览器 |

项目已附 `01_代码/frontend/dist` 网页构建文件，直接启动和验收无需安装 Node.js、npm 或 mongosh。首次安装 Python 依赖需要联网，请保留完整项目目录。

## 首次运行

### 1. 在项目目录打开 PowerShell

下载或解压项目后，在文件资源管理器中进入包含本 README、`01_代码`、`02_文档` 和 `03_测试` 的文件夹。

在文件资源管理器的地址栏输入 `powershell` 并按回车，终端会在当前文件夹打开。随后执行：

```powershell
Set-Location -LiteralPath ".\01_代码"
```

此时应能看到 `dev.ps1`、`backend` 和 `frontend`。如果终端已经位于 `01_代码`，无需再次切换目录。下文所有命令都在该目录执行。

### 2. 创建 Python 环境并安装依赖

按顺序执行，等待上一条完成后再执行下一条：

```powershell
py -3.13 -m venv ".\backend\.venv"
```

```powershell
& ".\backend\.venv\Scripts\python.exe" -m pip install -r ".\backend\requirements-lock.txt"
```

如果安装的是 Python 3.12，将第一条中的 `py -3.13` 改为 `py -3.12`。如果电脑没有 `py` 命令，但 `python --version` 显示为 3.12 或 3.13，也可以用 `python -m venv ".\backend\.venv"` 创建环境。

### 3. 生成本机配置

```powershell
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\setup_env.py" --mongodb-uri 'mongodb://127.0.0.1:27019/?replicaSet=rs0' --database survey_system_v2
```

脚本会生成 `backend/.env` 和随机签名密钥。已有配置不会被覆盖，也不需要复制开发者电脑上的配置。项目使用本机数据库端口 `27019`、副本集 `rs0` 和数据库 `survey_system_v2`。

### 4. 确认 MongoDB 程序位置

先检查系统能否找到 MongoDB：

```powershell
Get-Command mongod.exe -ErrorAction SilentlyContinue
```

如果能显示程序位置，可直接进入下一步。没有输出时，在本机 MongoDB Server 安装目录的 `bin` 文件夹中找到 `mongod.exe`，复制其完整路径，再执行：

```powershell
$env:MONGOD_BIN = (Read-Host "请输入本机 mongod.exe 的完整路径").Trim().Trim('"')
```

按提示粘贴本机路径并回车即可，路径中有空格也可以使用。此设置只对当前 PowerShell 窗口有效；新开窗口时需要重新设置，或者将 MongoDB 的 `bin` 目录加入系统 PATH。

### 5. 启动并打开网页

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev.ps1 start
```

该命令自动检查并启动本项目的 MongoDB、副本集和网页服务，无需另行启动数据库或手动初始化集合。等待出现：

```text
Application startup complete.
Uvicorn running on http://127.0.0.1:8001
```

保持此终端运行，在浏览器地址栏打开：

<http://127.0.0.1:8001>

看到登录页面即表示启动成功。项目没有预置账号，首次使用请自行注册。API 调试页为 <http://127.0.0.1:8001/docs>。

`ExecutionPolicy Bypass` 只对本次 PowerShell 进程生效，不修改系统执行策略。

## 再次启动

首次配置完成后，不需要重新安装依赖或生成配置。在项目根目录打开 PowerShell；如果 MongoDB 未加入 PATH，先按“首次运行”的第 4 步设置当前窗口的 `MONGOD_BIN`，然后执行：

```powershell
Set-Location -LiteralPath ".\01_代码"
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev.ps1 start
```

启动成功后打开 <http://127.0.0.1:8001>，并保留运行服务的终端。

如果提示“问序已经在运行，无需重复启动”，直接打开网页即可。本次没有新启动服务，可以关闭新开的终端，原服务所在的终端继续保留。

## 停止项目

1. 在运行项目的终端中按 **Ctrl+C**，等待返回 `PS ...>` 提示。
2. 在 `01_代码` 目录执行下面的命令，停止本项目数据库：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev.ps1 db-stop
```

看到 `Stopped this project's MongoDB; all data retained.` 后即可关闭终端。停止服务会保留账号、问卷和答卷，之后可以继续使用；仅关闭浏览器不会停止服务。

部分 Windows 终端在按 Ctrl+C 后会显示 `Command failed` 或退出码。如果已返回可输入命令的提示，继续执行数据库停止命令即可。

## 常见问题

| 情况 | 处理方法 |
|---|---|
| 找不到 `dev.ps1` | 确认终端位于 `01_代码`，该目录应同时包含 `backend`、`frontend` 和 `dev.ps1` |
| 找不到 `py` 或指定 Python 版本 | 安装 Python 3.12 或 3.13，或按实际版本调整创建虚拟环境的命令 |
| `Missing dependency`、缺少 Python 模块 | 完成“首次运行”第 2 步，并使用项目的虚拟环境安装依赖 |
| 缺少 `.env` | 执行“首次运行”第 3 步生成本机配置 |
| `MongoDB Server was not found` 或 `mongod not found` | 确认已安装 Server，按第 4 步设置实际的 `mongod.exe` 路径 |
| 缺少 `frontend/dist/index.html` | 使用包含网页构建文件的完整提交包；自行构建的方法见[前端说明](01_代码/frontend/README.md) |
| `Port 8001 is already in use` | 先打开本机网页地址；若不是正常问序服务，核对占用程序，并在其原终端停止冲突服务 |
| `27019` 被占用或服务身份不匹配 | 检查其他 MongoDB 实例的端口，不删除数据库文件或强制结束未知进程 |
| 浏览器提示拒绝连接或无法访问 | 确认已出现启动成功信息，运行终端仍打开，地址为 `http://127.0.0.1:8001` |

## 进入系统后怎么用

1. **注册并登录**：没有预置账号或问卷，首次进入空白工作空间是正常的。
2. **建题并组卷**：先在“我的题目”独立保存单选题、数字题等，再创建问卷，选择具体题目版本；按需配置必答、顺序和跳转。若想多次体验同一问卷，请在发布前开启“允许同一账号多次填写”。
3. **保存并发布**：保存草稿，点击“发布问卷”并确认。发布后题目、选项和跳转规则不能再修改。
4. **填写并提交**：复制分享链接，在浏览器打开，点击“开始填写”，逐题回答后点击“确认提交”。创建者可以填写自己的问卷，无需另注册账号；匿名问卷也需要登录。
5. **查看结果**：返回“我的问卷”查看本卷答卷和指标。另一份本人问卷引用同一版本并收集回答后，打开“跨问卷统计”，选择题目及版本核对合并结果。不同版本、恢复产生的新版本和独立副本分别统计。

未提交的填写答案不自动保存，刷新或离开页面后不能恢复。

## 项目说明与材料索引

本项目基于 MongoDB、FastAPI 和 React。新版规则见[业务规则](02_文档/06_新版业务规则.md)，已有测试结果及复现方法见[测试说明](03_测试/README.md)。

- [基础流程](02_文档/09_基础流程实现说明.md)：独立题目、不可变版本、问卷选版及管理写幂等。
- [管理能力](02_文档/10_管理能力实现说明.md)：共享授权、引用查询、个人题库及历史版本。
- [跨问卷统计](02_文档/11_跨问卷统计实现说明.md)与[旧问卷迁移工具](02_文档/12_旧问卷显式迁移说明.md)。
- AI 使用日志：[第一阶段](02_文档/05_AI使用日志.md)、[第二更新阶段](02_文档/13_第二阶段AI使用日志.md)，记录实际提问、AI 协助及使用者的判断和修正。
- [2.2 完整 OpenAPI](02_文档/附件/openapi-v2.json)与运行中的 [API 调试页](http://127.0.0.1:8001/docs)；[健康检查](http://127.0.0.1:8001/health)正常返回 `{"status":"ok"}`。

本仓库包含当前 2.2 版本及课程第一阶段保留材料。第二阶段项目完成报告位于本地 `04_项目完成报告`，按课程要求单独提交，不纳入 Git 仓库。基础与管理阶段的接口快照保留作历史记录。

| 入口 | 内容 |
|---|---|
| [01_代码](01_代码/) | 前后端源码、数据库定义和启动脚本；运行步骤见本 README |
| [02_文档](02_文档/) | 系统说明、数据库设计、API、关键逻辑和 AI 使用日志 |
| [03_测试](03_测试/README.md) | 分层测试方案、执行结果、原始证据和复现方法 |
| 04_项目完成报告（课程材料单独提交） | 本地第二阶段完成报告的 Markdown 及配图 |

仓库不包含真实 `.env`、虚拟环境、依赖目录和运行数据，运行后生成的这些文件已通过 `.gitignore` 排除。
