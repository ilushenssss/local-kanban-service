@echo off
setlocal
set "KANBAN_PY=%~dp0environment\python\pythonw.exe"
if exist "%KANBAN_PY%" goto run
set "KANBAN_PY=%~dp0.venv\Scripts\pythonw.exe"
if exist "%KANBAN_PY%" goto run
echo Python environment not found. See README.md.
pause
exit /b 1
:run
start "Kanban" "%KANBAN_PY%" "%~dp0main.pyw"
