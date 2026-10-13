@echo off
cd /d "%~dp0"
python app.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Demo could not start. Check the error above.
pause
exit /b 1
