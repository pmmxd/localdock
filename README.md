# LocalDock

简体中文 | [English](README.en.md)

LocalDock 是一个 Windows 本地静态网站绿色管理工具。管理端和静态服务使用 Python 标准库实现，配置存储在 SQLite 中；打包版不要求目标电脑安装 Python、Node.js 或数据库服务器。

## 功能

- 新增、编辑、复制和删除本地静态站点配置。
- 启动、停止、重启单个站点，或统一控制全部站点。
- 可逐个或批量勾选站点创建自定义组，组名可手动输入、自动生成并随时重命名；
  支持折叠、重新分组、移出分组和一键启动、停止整组。新增到已识别前后端子目录
  的服务会自动加入同一项目已有分组；只勾选一个服务也可以直接添加到已有分组，
  未分组服务还可从行内操作图标直接选择目标分组。
- 每个站点运行在独立子进程中。
- 自动分配唯一端口，默认从 `7000` 开始。
- 自动跳过常见 VPN、开发服务、数据库和远程管理端口。
- 允许手动指定端口，同时阻止站点端口重复。
- 打开浏览器、查看运行日志并回收失效的工作进程。
- 使用单个 SQLite 文件保存配置。
- 导出 CSV 配置并下载 SQLite 数据备份。
- 按站点启用受控的桌面能力：
  - 使用 Windows 默认软件打开文件或文件夹。
  - 在资源管理器中定位文件。
  - 将文件和目录移入 Windows 回收站。
  - 在授权目录内移动文件和目录。
  - 通过临时文件原子替换写入内容，并自动备份原文件。
  - 创建目录。
- 监控授权目录中的文件变化：
  - Windows 下优先使用 `ReadDirectoryChangesW` 系统通知。
  - 系统通知不可用时才低频轮询，最低间隔 3 秒。
  - 变化事件合并去抖后通过 SSE 推送。
  - 可选在站点文件变化后自动刷新页面。
- 异步运行站点白名单中的本地命令：
  - 支持 PATH 命令、可执行文件、`.cmd`、`.bat` 和 `.ps1` 封装工具。
  - 支持命名占位符参数模板。
  - 支持任务轮询、输出读取、取消、并发限制和超时。

## 快速开始

### 绿色版

获取或构建绿色版目录后运行：

```text
LocalDock.exe
```

管理端默认打开 `http://127.0.0.1:7080/`。如果端口被占用，LocalDock 会自动尝试后续端口。

### 源码运行

要求：

- Windows 10 或 Windows 11
- Python 3.11 或更高版本

```powershell
cd C:\path\to\localdock
python app.py
```

也可以双击 `start-localdock.bat`。

## 站点配置

每个静态站点包含：

- 显示名称。
- HTML 项目目录。
- 项目目录内的入口 HTML 文件。
- 唯一的本地 TCP 端口。
- 可选的启动后打开浏览器设置。

新建站点默认自动分配唯一端口；只有用户手动编辑端口时才使用指定端口。

## 桌面桥接

新建站点默认启用桌面能力，也可按站点单独关闭。

## 后端服务（Node / Python）

新增服务时选择“后端服务（Node / Python）”，即可由 LocalDock 直接管理
Node、Python 或其他本地运行程序。

支持项目内或绝对路径运行程序、启动参数、工作目录、`.env` 文件、健康检查路径、
启动/停止超时和可选的异常自动重启。LocalDock 会注入 `PORT`、检查健康接口、
收集 stdout/stderr、打开后端地址，并在停止时清理完整进程树。

LocalDock 不会自动执行 `npm install`、`pip install`、构建项目、代理
HTTP/WebSocket 流量，也不会把 Node 或 Python 全局打包进软件。项目文件、依赖、
`dist`、`data`、模型和媒体仍保留在项目目录中。

```json
{
  "runtime_path": "runtime/node.exe",
  "args": ["--enable-source-maps", "dist/server/index.js"],
  "cwd": "",
  "env_file": ".env",
  "health_path": "/api/health",
  "startup_timeout": 30,
  "shutdown_timeout": 5,
  "auto_restart": false,
  "restart_delay": 2
}
```

Python 后端示例：

```json
{
  "runtime_path": ".venv/Scripts/python.exe",
  "args": [
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    "{port}"
  ],
  "cwd": "",
  "env_file": ".env",
  "health_path": "/api/v1/health",
  "startup_timeout": 30,
  "shutdown_timeout": 5,
  "auto_restart": true,
  "restart_delay": 2
}
```

### 打开与定位

启用“本地文件操作”并选择允许访问的目录后，页面可以使用：

```text
GET  /api/status
POST /api/open
POST /__localdock/api/open
POST /api/open-with
POST /__localdock/api/open-with
POST /api/export
POST /__localdock/api/export
POST /api/export-to-folder
POST /__localdock/api/export-to-folder
POST /api/list
POST /__localdock/api/list
POST /api/read
POST /__localdock/api/read
POST /api/pick-folder
POST /__localdock/api/pick-folder
POST /api/pick-files
POST /__localdock/api/pick-files
POST /__localdock/api/reveal
GET  /__localdock/bridge.js
```

### 写操作

写操作需要额外启用“允许删除、移动和写入”：

```text
POST /api/copy
POST /__localdock/api/copy
POST /api/delete
POST /__localdock/api/trash
POST /__localdock/api/move
POST /__localdock/api/write
POST /__localdock/api/mkdir
```

安全行为：

- 相对路径在默认目录内解析；绝对路径可以访问其他系统位置。
- 接口仍只允许本站同源页面调用。
- 删除操作进入 Windows 回收站。
- 磁盘根目录和默认操作目录本身不能被删除、移动或覆盖。
- 移动操作不会覆盖已有目标。
- 修改已有文件必须传入 `"overwrite": true`。
- 创建新文件必须传入 `"create": true`。
- 修改已有文件前会先保留备份。
- 单次写入内容限制为 16 MB。
- 不允许通过桌面桥接打开可执行文件和脚本文件。
- `open-with` 会直接启动站点配置的外部软件，不再二次认证；未配置时才弹出系统选择器。
- `export` 每次都会显示系统保存对话框，只复制源文件，不修改原文件。
- `export-to-folder` 支持一次选择多个文件、只选择一次目标文件夹，并自动避让同名文件，绝不覆盖已有文件。

### JavaScript API

页面引入桥接脚本：

```html
<script src="/__localdock/bridge.js"></script>
```

LocalDock 会优先把桥接脚本注入到 `</head>` 前，页面内联脚本可直接使用 `LocalDock`，无需轮询等待。

```javascript
await LocalDock.open("documents/example.pdf");
await LocalDock.openWith("documents/example.pdf");
await LocalDock.export("documents/example.pdf", { open_with: true });
await LocalDock.exportToFolder(["documents/a.pdf", "documents/b.pdf"]);
const folder = await LocalDock.pickFolder();
const entries = await LocalDock.list(folder.path);
const text = await LocalDock.read(entries.entries[0].path);
await LocalDock.copy(entries.entries[0].path, "archive/copied.txt");
await LocalDock.reveal("documents/example.pdf");
await LocalDock.mkdir("archive");
await LocalDock.move("documents/example.pdf", "archive/example.pdf");
await LocalDock.write("archive/list.txt", "example.pdf", { create: true });
await LocalDock.trash("archive/list.txt");

const unwatch = LocalDock.watch((event) => {
  console.log(event);
});

await LocalDock.refresh();
unwatch();
```

## 文件监控

按站点启用“监控文件变化”：

- Windows 下优先使用系统通知。
- 监控目录自动使用本地文件操作授权目录，未启用时使用站点目录。
- 开启监控时页面自动刷新默认启用。
- 轮询仅作为回退方案，默认 10 秒。
- 轮询间隔最低 3 秒。
- 变化事件合并去抖后再推送。
- Windows 系统通知在空闲时阻塞等待；发布线程不再每 250 毫秒唤醒一次。
- 页面可通过 `LocalDock.watch(callback)` 订阅事件。
- 页面自动刷新仅响应站点目录内部的变化。

事件流接口：

```text
GET /__localdock/api/events
```

手动刷新接口：

```text
POST /__localdock/api/refresh
POST /api/rescan
```

LocalDock 负责检测文件变化并发送事件。书库索引等由项目自身生成的静态数据，仍需项目自己的生成器重新生成。

## 命令运行

命令运行默认关闭。启用“运行本地命令”后，使用 JSON 配置站点命令白名单：

```json
[
  {
    "id": "ffmpeg-convert",
    "label": "视频转码",
    "executable": "ffmpeg",
    "args": [
      "-hide_banner",
      "-y",
      "-i",
      "{input}",
      "-c:v",
      "libx264",
      "{output}"
    ],
    "cwd": "",
    "timeout": 3600,
    "allow_args": false,
    "detached": false
  }
]
```

启动任务并轮询状态：

```javascript
const { job } = await LocalDock.run("ffmpeg-convert", {
  input: "input/video.mp4",
  output: "output/video.mp4",
});

let result = await LocalDock.job(job.id);
while (["queued", "running"].includes(result.job.status)) {
  await new Promise((resolve) => setTimeout(resolve, 1000));
  result = await LocalDock.job(job.id);
}
```

取消任务：

```javascript
await LocalDock.cancelJob(job.id);
```

命令安全与限制：

- 网页只能调用站点白名单中登记过的命令。
- 命令通过 `subprocess` 和 `shell=False` 启动。
- 命令由固定可执行文件和参数数组组成。
- `{name}` 占位符用于接收网页参数。
- 仅当 `allow_args` 为 `true` 时，网页才允许追加参数。
- 批处理参数中的 `& | < > ^ % !` 会被拒绝。
- 每个站点最多并发 2 个命令。
- 队列最多保留 8 个任务。
- 超时上限为 3600 秒。
- GUI 外部软件把 `detached` 设为 `true`，启动后立即返回，不会被命令超时机制结束。
- 每个任务最多保留 256 KB 输出。
- 只保留最近 50 个任务。

## 数据与便携性

源码版数据保存在：

```text
data/
  localdock.db
  manager.token
  runtime.json
  logs/
  backups/
  file_backups/
```

打包版数据保存在 `LocalDock.exe` 同级目录。

软件不写注册表，也不写入 `%APPDATA%`。移动整个软件目录即可迁移配置。

## 安全模型

- 管理端和静态服务只监听 `127.0.0.1`。
- 修改管理配置的 API 需要本地管理令牌。
- 桌面桥接请求必须同源并使用 JSON。
- 相对路径限制在默认授权目录内；绝对路径可访问其他系统位置。
- 写权限与打开/定位权限分开。
- 命令权限单独配置，并采用站点命令白名单。
- 文件监控只读取文件元数据，不读取文件内容做比较。

这些限制可以降低风险，但写操作和命令执行仍然具备较强的本地能力，只应对可信页面和可信命令开启。

## 测试

```powershell
.\run-tests.ps1
```

测试覆盖配置校验、自动端口、静态文件服务、路径越界防护、桌面文件操作、Windows 回收站、文件监控、SSE 桥接、命令任务、取消、备份和 SQLite 操作。

## 构建

```powershell
.\build.ps1
```

构建产物：

```text
dist\LocalDock\LocalDock.exe
dist\LocalDock-portable.zip
```

PyInstaller 仅用于构建，最终绿色版不依赖 Python。

## 当前边界

- 主要支持平台为 Windows。
- LocalDock 不是通用应用服务器。
- 动态 API 兼容性取决于具体托管页面。
- 项目专用的目录生成器不会自动执行。
- 命令执行默认关闭；新建静态站点的桌面桥接、写入和文件监控默认启用，可逐站点关闭。

## 许可证

当前仓库尚未授予开源许可证。除后续添加许可证文件外，保留所有权利。
