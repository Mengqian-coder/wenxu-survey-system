# 问序前端

前端使用 React 19、Vite 7、React Router 和 Lucide 图标。提交副本已附 `dist`，可由后端直接托管；首次运行按 [安装与运行](../README.md) 操作即可。以下命令均从 **`01_代码` 目录**执行。

## 开发模式

修改前端时需要 Node.js。先保持后端运行在 8000 端口，再另开 PowerShell：

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev
```

打开 [开发页面](http://127.0.0.1:5173/)。Vite 支持热更新，将 `/api` 和 `/health` 代理到 `http://127.0.0.1:8000`，无需修改跨域配置。后端使用其他端口时，先设置代理目标，例如：

```powershell
$env:API_TARGET = "http://127.0.0.1:8001"
npm.cmd --prefix frontend run dev
```

## 构建

安装依赖后执行：

```powershell
npm.cmd --prefix frontend run build
```

产物写入 `frontend/dist`。后端启动时检测并挂载构建目录，首次构建后应重启后端；正常使用页面与 API 共用 8000 地址，无需单独运行 Vite。开发服务在对应终端按 `Ctrl+C` 停止。

## 源码与说明

`src/pages` 包含登录、工作空间、问卷编辑、填写和结果页面，`src/editor-utils.js`、`src/fill-utils.js` 存放编辑及填写辅助逻辑。界面与交互设计见 [前端设计](../../02_文档/附件/前端设计.md)，用户操作流程见 [系统说明](../../02_文档/01_系统说明.md)。

前端单元测试、真实浏览器验收的统一命令、环境要求和执行结果见 [测试说明](../../03_测试/README.md)。
