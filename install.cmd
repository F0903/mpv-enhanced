@echo off
setlocal

rem Fetches the pinned uv (see uv.pin) and hands over to the Python installer.
pushd "%~dp0" >nul || exit /b 1
set "PROJECT_ROOT=%CD%"
for /f "tokens=1,* delims==" %%A in (uv.pin) do set "PIN_%%A=%%B"
set "UV_VERSION=%PIN_version%"
set "UV_SHA256=%PIN_x86_64-pc-windows-msvc%"
set "UV_DIR=%PROJECT_ROOT%\.bootstrap\uv-%UV_VERSION%-windows-x86_64"
set "UV_BIN=%UV_DIR%\uv.exe"

if not exist "%UV_BIN%" (
    call :fetch_uv
    if errorlevel 1 (
        set "INSTALL_EXIT=1"
        goto :finish
    )
)

set "UV_PYTHON_INSTALL_DIR=%PROJECT_ROOT%\runtime\controller-python"
set "UV_PYTHON_NO_REGISTRY=1"
set "UV_CACHE_DIR=%PROJECT_ROOT%\.uv-cache"
"%UV_BIN%" run --project "%PROJECT_ROOT%" --locked --no-dev --no-editable mpv-enhanced %*
set "INSTALL_EXIT=%errorlevel%"

:finish
popd
rem Double-clicked from Explorer (no arguments, own console): keep the window
rem open so the result or error can be read.
if "%~1"=="" echo %cmdcmdline% | "%SystemRoot%\System32\findstr.exe" /i /c:"%~nx0" >nul && pause
exit /b %INSTALL_EXIT%

:fetch_uv
rem curl.exe and tar.exe ship with Windows 10 1803+; use them by full path so a
rem GNU tar from Git or MSYS on PATH (which cannot read zip files) is never picked.
set "UV_ZIP=%PROJECT_ROOT%\.bootstrap\uv-%UV_VERSION%-windows-x86_64.zip"
if not exist "%PROJECT_ROOT%\.bootstrap" mkdir "%PROJECT_ROOT%\.bootstrap" || exit /b 1
echo Downloading uv %UV_VERSION%...
"%SystemRoot%\System32\curl.exe" -fsSL -o "%UV_ZIP%" "https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"
if errorlevel 1 (
    echo error: could not download uv 1>&2
    exit /b 1
)
set "ACTUAL_SHA256="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%UV_ZIP%" SHA256') do (
    if not defined ACTUAL_SHA256 set "ACTUAL_SHA256=%%H"
)
set "ACTUAL_SHA256=%ACTUAL_SHA256: =%"
if /i not "%ACTUAL_SHA256%"=="%UV_SHA256%" (
    echo error: uv download has SHA-256 %ACTUAL_SHA256%, expected %UV_SHA256% 1>&2
    del "%UV_ZIP%"
    exit /b 1
)
if exist "%UV_DIR%.tmp" rmdir /s /q "%UV_DIR%.tmp"
mkdir "%UV_DIR%.tmp" || exit /b 1
"%SystemRoot%\System32\tar.exe" -xf "%UV_ZIP%" -C "%UV_DIR%.tmp"
if errorlevel 1 (
    echo error: could not extract uv 1>&2
    exit /b 1
)
del "%UV_ZIP%"
if exist "%UV_DIR%" rmdir /s /q "%UV_DIR%"
move "%UV_DIR%.tmp" "%UV_DIR%" >nul || exit /b 1
exit /b 0
