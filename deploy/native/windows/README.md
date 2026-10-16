# Windows 原生版（不依赖 Docker）

## 前置要求

- Python 3.12+（官网 https://www.python.org/downloads/，**安装时务必勾选 "Add python.exe to PATH"**）
- Node.js 18+（官网 https://nodejs.org/，一路下一步即可）

> 路径建议：把仓库放在**纯英文、无空格**的路径下，例如 `D:\kejixiaoyuan`。
> 中文或带空格的路径可能导致虚拟环境/依赖安装异常。

## 安装（只需一次）

双击 `deploy\native\windows\install.bat`，等待黑窗口显示"安装完成"。
重复双击不会报错。

## 启动

分别双击下面两个脚本（会各弹出一个黑窗口，保持开着）：

- `deploy\native\windows\start-api.bat` → 后端 API（http://127.0.0.1:8000）
- `deploy\native\windows\start-web.bat` → 前端页面（http://127.0.0.1:5173）

浏览器打开 http://127.0.0.1:5173 ，演示账号：`admin/admin123`（平台管理员）、
`tenant_admin/tenant123`、`expert/expert123`、`operator/operator123`。
关闭黑窗口即停止对应服务。

## 开机自启（可选）

用"任务计划程序"为 `start-api.bat` 和 `start-web.bat` 各建一个任务：

1. 打开"任务计划程序" → 创建任务
2. 触发器：登录时
3. 操作：启动程序 → 选择对应的 `.bat` 文件
4. 勾选"使用最高权限运行"（如需监听端口无阻）

## 常见问题

- **双击 bat 一闪而过**：脚本里已有 `pause`，若仍闪退请右键"以管理员身份运行"，
  或在 cmd 里手动执行看报错。
- **端口被占用**：`netstat -ano | findstr :8000` 查 PID 后在任务管理器结束，
  或改 bat 里的 `--port`。
- **想清空重来**：删除 `backend\data\` 目录，重新双击 `install.bat`。
- **杀毒软件误报**：Python 虚拟环境偶尔被误报，加白名单即可。
