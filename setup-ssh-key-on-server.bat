@echo off
setlocal

set SERVER_IP=141.164.40.19
set KEY_PATH=%USERPROFILE%\.ssh\vultr_api_gateway
set PUB_KEY_PATH=%USERPROFILE%\.ssh\vultr_api_gateway.pub

echo ========================================
echo One-time SSH Key Setup
echo ========================================
echo This registers your PC key on the server.
echo You should need the Vultr root password only once here.
echo.

if not exist "%KEY_PATH%" (
  echo Creating SSH key...
  ssh-keygen -t ed25519 -f "%KEY_PATH%" -C "vultr-api-gateway" -N ""
)

echo.
echo Uploading public key to server...
type "%PUB_KEY_PATH%" | ssh root@%SERVER_IP% "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
if errorlevel 1 goto failed

echo.
echo Testing key login...
ssh -i "%KEY_PATH%" root@%SERVER_IP% "echo SSH key login OK"
if errorlevel 1 goto failed

echo.
echo SUCCESS
echo From now on, deploy/status scripts should not ask for the root password.
pause
exit /b 0

:failed
echo.
echo FAILED
echo The key was not registered. Check the error above.
pause
exit /b 1
