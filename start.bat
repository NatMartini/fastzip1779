@echo off
setlocal EnableExtensions DisableDelayedExpansion
pushd "%~dp0" || exit /b 1

docker compose version >nul 2>&1
if errorlevel 1 (
    echo [FastZip] Install Docker Desktop with Docker Compose and make sure docker is on PATH.
    goto failed
)

docker info --format "{{.OSType}}" >nul 2>&1
if errorlevel 1 (
    echo [FastZip] Starting Docker Desktop...
    docker desktop start --timeout 120
    if errorlevel 1 (
        echo [FastZip] Open Docker Desktop, wait for it to start, then run start.bat again.
        goto failed
    )
)

set "FASTZIP_ENGINE="
for /f %%E in ('docker info --format "{{.OSType}}"') do set "FASTZIP_ENGINE=%%E"
if not "%FASTZIP_ENGINE%"=="linux" (
    echo [FastZip] Make sure Docker is running in Linux containers mode.
    goto failed
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    if errorlevel 1 goto failed
)

echo [FastZip] Building and starting the local stack. The first build can take several minutes.
docker compose up --build --detach --wait --wait-timeout 180
if errorlevel 1 (
    echo [FastZip] Compose could not start the stack. See the output above or run docker compose logs --tail=100.
    goto failed
)

set "FASTZIP_BOUND_PORT="
for /f "tokens=2 delims=:" %%P in ('docker compose port frontend 80') do set "FASTZIP_BOUND_PORT=%%P"
if not defined FASTZIP_BOUND_PORT (
    echo [FastZip] Could not find the frontend's published port.
    goto failed
)

set "FASTZIP_URL=http://localhost:%FASTZIP_BOUND_PORT%"
curl.exe --noproxy localhost --fail --silent --show-error --max-time 8 --retry 5 --retry-delay 2 --retry-connrefused "%FASTZIP_URL%/api/health/ready" >nul
if errorlevel 1 (
    echo [FastZip] The API is not responding at %FASTZIP_URL%. Check docker compose logs api frontend.
    goto failed
)

echo [FastZip] Ready at %FASTZIP_URL%
if /i not "%~1"=="--no-browser" start "" "%FASTZIP_URL%"
popd
exit /b 0

:failed
popd
if /i not "%~1"=="--no-browser" pause
exit /b 1
