@echo off
rem Double-click to start the Dwell web server and open the reader in your browser.
rem The server runs in this console window (you'll see the page logs + the URL);
rem close the window or press Ctrl-C to stop it.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

rem Open the default browser a few seconds after the server starts.
start "" cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8000/"

python "dwell_server.py"
echo.
echo ---- server stopped ----
pause >nul
