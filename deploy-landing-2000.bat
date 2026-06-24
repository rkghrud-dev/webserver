@echo off
setlocal

set SERVER_IP=141.164.40.19
set KEY_PATH=%USERPROFILE%\.ssh\vultr_api_gateway
set LOCAL_DIR=%USERPROFILE%\Desktop\서버\landing-page-2000
set REMOTE_DIR=/opt/landing-page-2000

echo ========================================
echo Deploy landing page to %SERVER_IP%:2000
echo ========================================
echo.
echo Uses SSH key: %KEY_PATH%
echo.

ssh -i "%KEY_PATH%" root@%SERVER_IP% "mkdir -p %REMOTE_DIR%"
if errorlevel 1 goto failed

scp -i "%KEY_PATH%" "%LOCAL_DIR%\index.html" "%LOCAL_DIR%\Dockerfile" "%LOCAL_DIR%\docker-compose.yml" root@%SERVER_IP%:%REMOTE_DIR%/
if errorlevel 1 goto failed

ssh -i "%KEY_PATH%" root@%SERVER_IP% "cd %REMOTE_DIR% && docker compose up -d --build && ufw allow 2000 && docker ps --filter name=landing-page-2000"
if errorlevel 1 goto failed

echo.
echo Done.
echo Open this URL:
echo http://%SERVER_IP%:2000
echo.
pause
exit /b 0

:failed
echo.
echo Deploy failed. Check the message above.
pause
exit /b 1
