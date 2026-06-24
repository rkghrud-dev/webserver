@echo off
setlocal

set SERVER_IP=141.164.40.19
set KEY_PATH=%USERPROFILE%\.ssh\vultr_api_gateway
set LOCAL_DIR=C:\Users\rkghr\server-workspace\landing-page-2000
set REMOTE_DIR=/opt/landing-page-2000

echo ========================================
echo Landing Page 2000 Deploy
echo ========================================
echo This will upload files to the server.
echo Uses SSH key: %KEY_PATH%
echo.

echo [1/4] Create server folder
ssh -i "%KEY_PATH%" root@%SERVER_IP% "mkdir -p %REMOTE_DIR%"
if errorlevel 1 goto failed

echo.
echo [2/4] Upload files
scp -i "%KEY_PATH%" "%LOCAL_DIR%\index.html" root@%SERVER_IP%:%REMOTE_DIR%/index.html
if errorlevel 1 goto failed
scp -i "%KEY_PATH%" "%LOCAL_DIR%\Dockerfile" root@%SERVER_IP%:%REMOTE_DIR%/Dockerfile
if errorlevel 1 goto failed
scp -i "%KEY_PATH%" "%LOCAL_DIR%\docker-compose.yml" root@%SERVER_IP%:%REMOTE_DIR%/docker-compose.yml
if errorlevel 1 goto failed

echo.
echo [3/4] Start Docker container and open firewall
ssh -i "%KEY_PATH%" root@%SERVER_IP% "cd %REMOTE_DIR% && docker compose up -d --build && ufw allow 2000"
if errorlevel 1 goto failed

echo.
echo [4/4] Test
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 2; try { Invoke-WebRequest -Uri 'http://%SERVER_IP%:2000' -UseBasicParsing -TimeoutSec 10 | Select-Object -ExpandProperty StatusCode } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 goto failed

echo.
echo SUCCESS
echo Open:
echo http://%SERVER_IP%:2000
start "" "http://%SERVER_IP%:2000"
pause
exit /b 0

:failed
echo.
echo FAILED
echo The page was not deployed. Check the error above.
pause
exit /b 1
