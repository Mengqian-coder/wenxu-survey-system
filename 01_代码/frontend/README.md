# 问序前端

前端使用 React 19、Vite 7、React Router 和 Lucide 图标。提交副本已附 `dist`，可由后端直接托管；首次运行按 [安装与运行](../../README.md) 操作即可。以下命令均从 **`01_代码` 目录**执行。

## 开发模式

直接体验已有页面只需按 [安装与运行](../../README.md) 执行 `dev.ps1 start`，访问 `8001`，无需启动 Vite。修改前端并使用热更新时才需要 Node.js。先从 `01_代码` 启动项目并保持终端打开：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev.ps1 start
```

再另开 PowerShell，首次安装前端依赖后启动开发页面：

```powershell
npm.cmd --prefix frontend ci
powershell -NoProfile -ExecutionPolicy Bypass -File .\dev.ps1 frontend
```

该入口检查 `8001` 后端的健康状态，然后运行 [开发页面](http://127.0.0.1:5175/)。Vite 支持热更新，将 `/api` 和 `/health` 代理到 `http://127.0.0.1:8001`，无需修改跨域配置。`5175` 被占用时直接退出，不自动切换端口。

已有后端正常运行时，也可执行 `npm.cmd --prefix frontend run dev`，默认使用相同的端口和代理。若手动使用其他后端端口，先设置 `API_TARGET`，例如：

```powershell
$env:API_TARGET = "http://127.0.0.1:8010"
npm.cmd --prefix frontend run dev
```

## 构建

安装依赖后执行：

```powershell
npm.cmd --prefix frontend run build
```

产物写入 `frontend/dist`。后端启动时检测并挂载构建目录，首次构建后应重启后端；正常使用页面与 API 共用 `8001` 地址，无需单独运行 Vite。开发服务在对应终端按 `Ctrl+C` 停止。

## 源码与说明

`src/pages` 包含登录、工作空间、问卷编辑、填写和结果页面，`src/editor-utils.js`、`src/fill-utils.js` 存放编辑及填写辅助逻辑。界面与交互设计见 [前端设计](../../02_文档/附件/前端设计.md)，用户操作流程见 [系统说明](../../02_文档/01_系统说明.md)。

前端单元测试、真实浏览器验收的统一命令、环境要求和执行结果见 [测试说明](../../03_测试/README.md)。
