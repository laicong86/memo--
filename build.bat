@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY39=C:\Users\laicong\AppData\Local\Programs\Python\Python39\python.exe"

echo Building MemoTool.exe with system Python 3.9 ...

if not exist "build" mkdir "build"
if exist "dist\MemoTool.exe" move /Y "dist\MemoTool.exe" "build\MemoTool.previous.exe" >nul

if exist "%PY39%" (
    "%PY39%" -m PyInstaller --onefile --noconsole --clean --noupx --name MemoTool main.py
) else (
    py -3.9 -m PyInstaller --onefile --noconsole --clean --noupx --name MemoTool main.py
)

echo.
echo Done. Output: dist\MemoTool.exe
pause
endlocal
