# 问序后端

后端使用 FastAPI 和同步 PyMongo，MongoDB 副本集提供事务支持。安装、数据库管理和服务启动统一见 [安装与运行](../README.md)。

## 源码入口

| 文件 | 职责 |
|---|---|
| `app/main.py` | 路由、鉴权入口和错误响应 |
| `app/models.py` | 请求结构、严格类型和字段限制 |
| `app/domain.py` | 题目配置、答案标准化和跳转路径校验 |
| `app/service.py` | 权限、问卷状态、提交事务、答卷查询和统计 |
| `app/security.py` | 密码哈希及登录、填写凭据 |
| `app/bootstrap.py` | 集合验证器和索引初始化 |
| `app/frontend.py` | 前端静态资源托管和页面路由回退 |

业务契约见 [API 说明](../../02_文档/03_API说明.md)，算法与事务流程见 [关键逻辑](../../02_文档/04_关键逻辑说明.md)。

## 配置

`setup_env.py` 在本机生成 `backend/.env`，已有文件不覆盖。`SECRET_KEY` 更换后已有凭据失效，不应随材料提交。

| 环境变量 | 默认值或用途 |
|---|---|
| `SECRET_KEY` | 自动生成随机值，至少 64 字符，不接受模板占位值 |
| `MONGODB_URI` | `mongodb://127.0.0.1:27018/?replicaSet=rs0` |
| `MONGODB_DATABASE` | `survey_system` |
| `ACCESS_TOKEN_MINUTES` | `120`，登录凭据有效期（分钟） |
| `SUBMISSION_TOKEN_MINUTES` | `1440`，填写凭据有效期（分钟） |
| `AUTO_INIT_DB` | `true`，启动时应用集合验证器和索引 |
| `CORS_ORIGINS` | `[]`，允许跨域来源的 JSON 数组；同源页面无需修改 |
| `MAX_REQUEST_BYTES` | `12582912`（12 MiB），请求体上限 |

`requirements-lock.txt` 固定已验证的依赖版本；`requirements.txt` 提供允许范围，主动升级后需重新验证。

## 数据库定义维护

正常启动读取同级 `database/schema-definitions.json`，创建或更新四集合验证器和六个业务索引，无需 Node.js 或 mongosh。初始化不插入示例数据，也不迁移、清理或修复历史数据。

如需单独初始化受本项目管理的开发实例，从 **`01_代码`** 执行：

```powershell
& ".\backend\.venv\Scripts\python.exe" ".\backend\scripts\dev_mongo.py" init
```

默认初始化 `survey_system`；其他端口或数据库需加对应的 `--port`、`--database`。自行管理的实例应按 `.env` 配置，在 `backend` 目录用虚拟环境 Python 执行 `-m app.bootstrap`。

修改 `database/init-mongodb.js` 后，在 `01_代码` 同步并检查 JSON 定义：

```powershell
node database/export-schema.js
node database/export-schema.js --check
```

字段与建模理由见 [数据库设计](../../02_文档/02_数据库设计.md)，测试运行及结果见 [测试说明](../../03_测试/README.md)。
