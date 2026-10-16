@echo off
REM =============================================================================
REM 科技小院平台 · Windows 一键安装脚本（纯本机模式，不依赖 Docker）
REM
REM 用法：双击本文件，或在仓库根目录执行 install.bat
REM 幂等：重复运行不会报错（venv 已存在则跳过创建，.env 已存在则不覆盖，
REM       alembic/seed 本身幂等）。
REM =============================================================================
setlocal EnableDelayedExpansion

REM 仓库根目录（脚本位于 deploy\native\windows\，向上三级即仓库根）
set "REPO_DIR=%~dp0..\..\.."
pushd "%REPO_DIR%"
echo ==^> 仓库目录：%CD%

REM ---------- 1. 检查 Python（优先 py -3.12，回退 python） ----------
set "PYCMD="
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
  set "PYCMD=py -3.12"
) else (
  python --version >nul 2>&1
  if not errorlevel 1 (
    set "PYCMD=python"
  )
)
if "%PYCMD%"=="" (
  echo [错误] 未找到 Python 3.12+，请先从 https://www.python.org/downloads/ 安装，
  echo        安装时务必勾选 "Add python.exe to PATH"。
  pause
  exit /b 1
)
echo ==^> 使用 %PYCMD%

REM ---------- 2. 检查 node ----------
node --version >nul 2>&1
if errorlevel 1 (
  echo [错误] 未找到 node，请先从 https://nodejs.org/ 安装 Node.js 18+。
  pause
  exit /b 1
)
echo ==^> node 版本：
node --version

REM ---------- 3. 创建虚拟环境（已存在则跳过） ----------
if exist ".venv\Scripts\python.exe" (
  echo ==^> .venv 已存在，跳过创建
) else (
  echo ==^> 创建虚拟环境 .venv ...
  %PYCMD% -m venv .venv
)

REM ---------- 4. 安装后端依赖 ----------
echo ==^> 安装后端依赖 ...
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\python -m pip install -e ".[dev]" -q

REM ---------- 5. 安装前端依赖 ----------
echo ==^> 安装前端依赖 ...
cd frontend
call npm install
cd ..

REM ---------- 6. 复制后端 env（已存在则不覆盖） ----------
if exist "backend\.env" (
  echo ==^> backend\.env 已存在，跳过复制
) else (
  echo ==^> 复制 backend\.env.example -^> backend\.env
  copy "backend\.env.example" "backend\.env" >nul
)

REM ---------- 7. 数据库迁移 ----------
echo ==^> 执行数据库迁移 ...
cd backend
..\.venv\Scripts\python -m alembic upgrade head

REM ---------- 8. 灌演示数据（幂等） ----------
echo ==^> 灌演示数据 ...
..\.venv\Scripts\python -m app.db.seed
cd ..

echo.
echo ================================================================
echo  安装完成！双击下面两个脚本分别启动后端和前端：
echo.
echo    deploy\native\windows\start-api.bat  （后端 API）
echo    deploy\native\windows\start-web.bat  （前端页面）
echo.
echo  然后浏览器打开 http://127.0.0.1:5173 ，用 admin / admin123 登录。
echo ================================================================
popd
pause
endlocal
