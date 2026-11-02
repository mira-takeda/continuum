@echo off
rem Continuum one-click launcher (Windows). Double-click to run.
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [Continuum] Python was not found on PATH.
  echo Install Python 3.10+ from https://www.python.org/downloads/ and retry.
  pause
  exit /b 1
)
powershell -NoProfile -Command "try { $q=[Net.WebRequest]::Create('http://127.0.0.1:8137/api/ui/bootstrap'); $q.Proxy=$null; $q.Timeout=4000; $s=New-Object IO.StreamReader($q.GetResponse().GetResponseStream()); if ($s.ReadToEnd() -match 'runtime') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>nul
if not errorlevel 1 (
  echo [Continuum] A Continuum server is already running. Opening browser...
  start "" "http://127.0.0.1:8137/"
  exit /b 0
)
echo [Continuum] Starting server...
start "Continuum server" python -u server.py
echo [Continuum] Waiting for http://127.0.0.1:8137/ ...
powershell -NoProfile -Command "$ok=$false; for ($i=0; $i -lt 30; $i++) { try { $q=[Net.WebRequest]::Create('http://127.0.0.1:8137/api/ui/bootstrap'); $q.Proxy=$null; $q.Timeout=2500; $q.GetResponse().Close(); $ok=$true; break } catch { Start-Sleep -Seconds 1 } }; if (-not $ok) { exit 1 }" >nul 2>nul
if errorlevel 1 (
  echo [Continuum] The server did not respond within 30 seconds.
  echo Check the "Continuum server" window for errors.
  pause
  exit /b 1
)
start "" "http://127.0.0.1:8137/"
echo [Continuum] Browser opened. Close the "Continuum server" window to stop.
endlocal
