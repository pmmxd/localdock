# LocalDock

English | [简体中文](README.md)

LocalDock is a portable Windows application for managing local static websites. It uses the Python standard library for the server and SQLite for configuration, so the packaged application does not require Python, Node.js, or a database server to be installed.

## Features

- Add, edit, copy, and remove local static site configurations.
- Start, stop, and restart individual sites, or control all sites together.
- Select services individually or in batches to create custom groups; group
  names can be entered or generated automatically, renamed later, collapsed,
  removed, and started or stopped together. Newly added frontend/backend
  services in recognized project subdirectories join the same manual group
  automatically. One selected service can be added directly to an existing
  group, including from its row action menu.
- Run each site in an independent child process.
- Automatically assign unique ports starting at `7000`.
- Skip common VPN, development, database, and remote-management ports.
- Allow manual port overrides while rejecting duplicate site ports.
- Open the site in a browser, inspect service logs, and recover stale workers.
- Store configuration in a single portable SQLite database.
- Export site configuration as CSV and download SQLite backups.
- Enable controlled desktop integration per site:
  - Open files or folders with the Windows default application.
  - Reveal files in File Explorer.
  - Move files to the Windows Recycle Bin.
- Move files and directories using approved relative paths.
  - Atomically write files with automatic backups.
  - Create directories.
- Watch approved directories for changes:
  - Prefer the Windows `ReadDirectoryChangesW` notification API.
  - Fall back to low-frequency polling with a minimum 3-second interval.
  - Debounce and stream change events over Server-Sent Events (SSE).
  - Optionally reload the page when site files change.
- Run allow-listed local commands asynchronously:
  - Supports PATH commands, executables, `.cmd`, `.bat`, and `.ps1` wrappers.
  - Uses argument templates with named placeholders.
  - Supports job polling, output capture, cancellation, concurrency limits, and timeouts.

## Quick Start

### Portable Build

Build or obtain the portable directory, then run:

```text
LocalDock.exe
```

The manager opens at `http://127.0.0.1:7080/`. If that port is occupied, LocalDock tries the following ports.

### Source

Requirements:

- Windows 10 or Windows 11
- Python 3.11 or newer

```powershell
cd C:\path\to\localdock
python app.py
```

You can also run `start-localdock.bat`.

## Service Configuration

Each static site has:

- A display name.
- A project directory.
- An entry HTML file inside the project directory.
- A unique local TCP port.
- An optional auto-open setting.

New sites use automatic unique port allocation unless the port was explicitly edited.

## Desktop Bridge

Desktop integration is enabled by default for newly created sites and can be disabled per site.

## Backend Services (Node / Python)

Create a service and select **Backend service (Node / Python)** to let LocalDock
manage Node, Python or another local runtime directly.

The configuration supports a project-local or absolute runtime path, startup
arguments, working directory, `.env` file, health path, startup/shutdown timeouts
and optional automatic restart. LocalDock injects `PORT`, checks the health
endpoint, captures stdout/stderr, opens the backend URL and stops the full
process tree.

LocalDock does not run `npm install`, `pip install`, build the project, proxy
HTTP/WebSocket traffic or bundle Node/Python globally. Project files,
dependencies, `dist`, `data`, models and media remain inside the project
directory.

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

Python backend example:

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

### Read and Open

Enable **Local file operations** and choose an allowed directory. The site can then use:

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

### Write Operations

Write operations require the separate **Allow delete, move, and write** permission.

```text
POST /api/copy
POST /__localdock/api/copy
POST /api/delete
POST /__localdock/api/trash
POST /__localdock/api/move
POST /__localdock/api/write
POST /__localdock/api/mkdir
```

Safety behavior:

- Relative paths resolve inside the default directory; absolute paths can access other system locations.
- Requests remain restricted to the local site origin.
- Deletes use the Windows Recycle Bin.
- Drive roots and the default directory itself cannot be deleted, moved, or overwritten.
- Moves never overwrite an existing destination.
- Existing files require `"overwrite": true`.
- New files require `"create": true`.
- Modified files are backed up before replacement.
- Writes are limited to 16 MB per request.
- Executable and script file extensions cannot be opened through the desktop bridge.
- `open-with` launches the configured external application without a second confirmation; without one it shows the system picker.
- `export` always shows a native save dialog and copies the source without changing it.
- `export-to-folder` accepts one or more files, asks for one destination folder, and never overwrites an existing file.

### JavaScript API

Include the bridge script:

```html
<script src="/__localdock/bridge.js"></script>
```

LocalDock injects this script before `</head>` when possible, so page inline scripts can use `LocalDock` immediately without polling.

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

## File Monitoring

Enable **Watch for file changes** per site.

- Windows notifications are used when available.
- The watched folder defaults to the file-operation root, then the site directory.
- Page auto-reload is enabled by default when monitoring is turned on.
- Polling is only a fallback and defaults to 10 seconds.
- The polling interval cannot be lower than 3 seconds.
- Events are debounced before they are published.
- Native notification waiting is idle-blocking; the publisher thread no longer wakes every 250 ms.
- Pages can subscribe with `LocalDock.watch(callback)`.
- Automatic page reload applies only to changes inside the site directory.

The event stream endpoint is:

```text
GET /__localdock/api/events
```

Manual refresh endpoints:

```text
POST /__localdock/api/refresh
POST /api/rescan
```

File changes are detected and published by LocalDock. Project-specific generated indexes still need to be rebuilt by the project's own generator.

## Command Runner

Command execution is disabled by default. Enable **Run local commands** and define an allow-list as JSON.

```json
[
  {
    "id": "ffmpeg-convert",
    "label": "Transcode video",
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

Run and poll a job:

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

Cancel a job:

```javascript
await LocalDock.cancelJob(job.id);
```

Command safety and limits:

- Webpages can only call commands registered in the site allow-list.
- Commands are launched with `subprocess` and `shell=False`.
- A command uses a fixed executable and an argument list.
- `{name}` placeholders receive values from the page.
- Additional page arguments are rejected unless `allow_args` is `true`.
- Batch-file arguments containing `& | < > ^ % !` are rejected.
- Maximum concurrency is 2 commands per site.
- Maximum queue size is 8 jobs.
- Timeouts are limited to 3600 seconds.
- Set `detached` to `true` for GUI applications so launch returns immediately instead of waiting for the app to exit.
- Output is capped at 256 KB per job.
- Only the 50 most recent jobs are retained.

## Data and Portability

For the source version, data is stored in:

```text
data/
  localdock.db
  manager.token
  runtime.json
  logs/
  backups/
  file_backups/
```

For the packaged version, data is stored next to `LocalDock.exe`.

The application does not write to the registry or `%APPDATA%`. Move the entire application directory to move its configuration.

## Security Model

- The manager and static servers bind to `127.0.0.1` only.
- Mutating manager APIs require a local manager token.
- Desktop bridge requests must be same-origin and use JSON.
- Relative paths stay inside the configured root; absolute paths can access other system locations.
- Write permission is separate from read/open permission.
- Command permission is separate and uses a per-site allow-list.
- File monitoring is metadata-only and does not read file contents for comparison.

These controls reduce risk, but enabling write access or command execution still grants powerful local capabilities. Enable them only for trusted local pages and commands.

## Tests

```powershell
.\run-tests.ps1
```

The test suite covers configuration validation, automatic port allocation, static file serving, path traversal protection, desktop file operations, Windows Recycle Bin deletion, file monitoring, SSE bridge helpers, command jobs, cancellation, backups, and SQLite operations.

## Build

```powershell
.\build.ps1
```

Build outputs:

```text
dist\LocalDock\LocalDock.exe
dist\LocalDock-portable.zip
```

PyInstaller is a build-time dependency only. The resulting portable directory does not require Python.

## Current Limitations

- Windows is the primary supported platform.
- LocalDock is not a general-purpose application server.
- Dynamic API compatibility depends on the hosted page.
- Project-specific catalog generators are not automatically executed.
- Command execution is opt-in; desktop integration, writes, and file monitoring are enabled by default for new static sites and can be disabled per site.

## License

No license has been granted for this repository. All rights are reserved unless a license file is added.
