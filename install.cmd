@echo off
setlocal

pushd "%~dp0" >nul || exit /b 1
set "PROJECT_ROOT=%CD%"
set "UV_BIN=%PROJECT_ROOT%\uv.exe"

if not exist "%UV_BIN%" (
    where uv.exe >nul 2>nul
    if errorlevel 1 (
        echo uv.exe was not found beside this launcher or on PATH.
        echo Download the bootstrap archive from the mpv-enhanced release page,
        echo or install uv from https://docs.astral.sh/uv/.
        popd
        exit /b 1
    )
    set "UV_BIN=uv.exe"
)

set "MPV_ENHANCED_UV=%UV_BIN%"
set "UV_PYTHON_INSTALL_DIR=%PROJECT_ROOT%\runtime\controller-python"
set "UV_PYTHON_NO_REGISTRY=1"
set "UV_CACHE_DIR=%PROJECT_ROOT%\.uv-cache"
"%UV_BIN%" run --project "%PROJECT_ROOT%" --locked --no-dev --no-editable mpv-enhanced %*
set "INSTALL_EXIT=%errorlevel%"
popd
exit /b %INSTALL_EXIT%
