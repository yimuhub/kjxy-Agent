@echo off
REM 科技小院平台 · Windows 启动前端页面（http://127.0.0.1:5173）
REM 关闭本窗口即停止服务。
setlocal
cd /d "%~dp0..\..\.."
cd frontend
call npm run dev
echo.
echo [前端已退出]
pause
endlocal
