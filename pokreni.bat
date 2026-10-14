@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "URL=http://127.0.0.1:8765"

curl -sf "%URL%/api/status" >nul 2>&1
if not errorlevel 1 (
  start "" "%URL%"
  exit /b 0
)

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import fastapi, onnxruntime, PIL" >nul 2>&1
  if not errorlevel 1 goto run
)

if exist ".venv" rmdir /s /q ".venv"

set "PY="
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3.12"
if not defined PY (
  py -3 -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  echo Instaliraj Python 3.12 sa https://www.python.org/downloads/
  echo Pri instalaciji cekiraj "Add python.exe to PATH".
  pause
  exit /b 1
)

echo Prvi put instaliram biblioteke. Potreban je internet, moze da potraje nekoliko minuta.
%PY% -m venv .venv
if errorlevel 1 (
  echo Nije uspelo pravljenje okruzenja.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Instalacija nije uspela. Proveri internet i pokusaj ponovo.
  pause
  exit /b 1
)

:run
set "NO_BROWSER=1"
powershell -NoProfile -Command "Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList 'app.py' -WorkingDirectory '%CD%' -WindowStyle Hidden -RedirectStandardOutput 'pozadina.log' -RedirectStandardError 'pozadina.err.log'"

set /a tries=0
:wait
set /a tries+=1
if %tries% gtr 40 goto fail
curl -sf "%URL%/api/status" >nul 2>&1
if not errorlevel 1 goto opened
timeout /t 1 /nobreak >nul
goto wait

:opened
start "" "%URL%"
echo Pozadina radi. Ovaj prozor mozes zatvoriti.
timeout /t 4 /nobreak >nul
exit /b 0

:fail
echo Program se nije pokrenuo.
if exist pozadina.err.log (
  echo --- pozadina.err.log ---
  powershell -NoProfile -Command "Get-Content -Path 'pozadina.err.log' -Tail 30"
)
pause
exit /b 1
