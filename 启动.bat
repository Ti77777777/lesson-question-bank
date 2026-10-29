@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "launch_flags="
if not "%~1"=="" goto run

title 习题课题库系统
echo.
echo   习题课题库系统
echo   ================================
echo   [1] 打开题库（检索、组题、讲义排版）
echo   [2] 打开 AI 工作台（上传、识别、审校）
echo   [3] 体验演示题库（独立的虚构样题）
echo   [0] 退出
echo.
echo   1 和 2 共用当前题库；3 使用独立演示数据。
echo.
choice /c 1230 /n /m "请选择 [1/2/3/0]："
if errorlevel 4 exit /b 0
if errorlevel 3 goto demo
if errorlevel 2 goto ai
if errorlevel 1 goto run
exit /b 1

:ai
set "launch_flags=--ai"
goto run

:demo
set "launch_flags=--demo"

:run
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 run.py %launch_flags% %*
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo 未找到 Python。请先安装 Python 3.10 或更高版本，并添加到 PATH。
        pause
        exit /b 1
    )
    python run.py %launch_flags% %*
)
set "launch_exit=%errorlevel%"
if not "%launch_exit%"=="0" pause
exit /b %launch_exit%
