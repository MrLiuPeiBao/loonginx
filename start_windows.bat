@echo off
setlocal

cd /d "%~dp0"

REM Prefer system Python so run_all.py can repair/recreate venv_widows on a new machine.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.11 run_all.py
  goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
  python run_all.py
  goto :done
)

echo [ERROR] Python not found.
echo Please install Python 3.11 and ensure it is available in PATH (or install the Windows "py" launcher).
pause

:done
endlocal
