# 问序｜测试材料

整理日期：2026 年 10 月 19 日。测试源码保留在 `01_代码`，本目录保存设计、结果和执行证据。

| 材料 | 内容 |
|---|---|
| [测试计划与方法](01_测试计划与方法.md) | 测试分层、代表用例、断言与隔离方法 |
| [用例覆盖表](02_用例覆盖表.md) | 前提、输入、步骤、预期及源码映射 |
| [执行结果](03_执行结果.md) | 分阶段实际结果、环境、证据与限制 |
| [关闭与提交竞争专项](04_关闭与提交竞争专项.md) | 两个受控顺序、四种策略组合和时序证据 |
| [AI 使用日志](../02_文档/05_AI使用日志.md) | AI 协助过程及使用者的判断、修正 |

## 统一复现入口

先按[代码配置说明](../01_代码/README.md)安装 Python 依赖和 MongoDB 程序。在 `01_代码` 目录执行后端测试：

```powershell
backend/.venv/Scripts/python.exe backend/scripts/run_tests.py --suite backend
```

运行全部套件还需 Node.js、前端依赖和 Microsoft Edge：

```powershell
npm.cmd --prefix frontend ci
backend/.venv/Scripts/python.exe backend/scripts/run_tests.py --suite all
```

`--suite frontend`、`--suite e2e` 可单独运行相应套件；专项筛选命令见[专项说明](04_关闭与提交竞争专项.md)。找不到 `mongod` 时，将 `MONGOD_BIN` 设置为实际程序路径。

[运行器](../01_代码/backend/scripts/run_tests.py)与[数据库 fixture](../01_代码/backend/tests/conftest.py)启动独立副本集及测试服务，不读取真实 `.env`，不连接日常开发数据库，结束后清理自己的进程和测试库。每次输出写入新的 `01_代码/output/test-runs/<本次标识>/`，不会覆盖本目录已有证据。

结果按参数化展开后的执行项计数，不能用覆盖表行数、断言数或不同轮次之和代替。