@echo off
setlocal
set "KANBAN_PY=%~dp0environment\python\python.exe"
if exist "%KANBAN_PY%" goto run
set "KANBAN_PY=%~dp0.venv\Scripts\python.exe"
if exist "%KANBAN_PY%" goto run
echo Python environment not found. See README.md.
pause
exit /b 1
:run
"%KANBAN_PY%" "%~dp0main.pyw"
pause
