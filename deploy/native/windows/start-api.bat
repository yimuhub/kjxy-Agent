@echo off
REM 科技小院平台 · Windows 启动后端 API（http://127.0.0.1:8000）
REM 关闭本窗口即停止服务。
setlocal
cd /d "%~dp0..\..\.."
cd backend
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
echo [后端已退出]
pause
endlocal
