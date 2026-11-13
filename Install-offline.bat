@echo off
setlocal
pushd "%~dp0" || exit /b 1
py -3.8 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install --no-index --find-links wheelhouse --require-hashes -r requirements\windows.lock
if errorlevel 1 goto fail
echo Installation complete. Run Kanban.bat.
popd
pause
exit /b 0
:fail
echo Installation failed. See README.md.
popd
pause
exit /b 1
