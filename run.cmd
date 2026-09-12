@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "UV="
where uv >nul 2>nul && for /f "delims=" %%I in ('where uv') do (
  if not defined UV set "UV=%%I"
)
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%USERPROFILE%\.cargo\bin\uv.exe" set "UV=%USERPROFILE%\.cargo\bin\uv.exe"

if not defined UV (
  echo uv was not found.
  echo Install: https://docs.astral.sh/uv/
  echo Or add uv.exe to PATH, then run this file again.
  pause
  exit /b 1
)

"%UV%" run python -m mp4adpladder %*
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo MP4ADPLADDER exited with code %ERR%
  pause
)
exit /b %ERR%
