param([string]$Bundle = "")
$ErrorActionPreference = 'Stop'
$nervRoot = if ($env:NERV_HOME) { $env:NERV_HOME } else { Join-Path $env:USERPROFILE '.nerv' }
$env:NERV_HOME = $nervRoot
$releaseBase = if ($env:NERV_RELEASE_BASE) { $env:NERV_RELEASE_BASE } else { 'https://github.com/Yanshi-Robotics/nerv/releases/latest/download' }
New-Item -ItemType Directory -Force -Path "$nervRoot/bin", "$nervRoot/bootstrap" | Out-Null
$env:UV_UNMANAGED_INSTALL = "$nervRoot/bin"
if (-not (Test-Path "$nervRoot/bin/uv.exe")) { Invoke-RestMethod 'https://astral.sh/uv/0.12.2/install.ps1' | Invoke-Expression }
if ($Bundle) {
  Copy-Item "$Bundle/bootstrap.py" "$nervRoot/bootstrap/install.py"
  Copy-Item "$Bundle/launch.py" "$nervRoot/bootstrap/launch.py"
  $manifest = Join-Path $Bundle 'release.json'
} else {
  Invoke-WebRequest "$releaseBase/bootstrap.py" -OutFile "$nervRoot/bootstrap/install.py"
  Invoke-WebRequest "$releaseBase/release.json" -OutFile "$nervRoot/bootstrap/release.json"
  Invoke-WebRequest "$releaseBase/launch.py" -OutFile "$nervRoot/bootstrap/launch.py"
  $manifest = "$nervRoot/bootstrap/release.json"
}
$env:UV_PYTHON_INSTALL_DIR = "$nervRoot/python"
$env:UV_CACHE_DIR = "$nervRoot/.cache/uv"
$env:UV_CREDENTIALS_DIR = "$nervRoot/.cache/uv-credentials"
& "$nervRoot/bin/uv.exe" run --managed-python --no-project --python 3.12 "$nervRoot/bootstrap/install.py" --manifest "$manifest"
if ($LASTEXITCODE -ne 0) { throw 'NERV installation failed' }
@'
@echo off
if not defined NERV_HOME set "NERV_HOME=%USERPROFILE%\.nerv"
set "UV_PYTHON_INSTALL_DIR=%NERV_HOME%\python"
set "UV_CACHE_DIR=%NERV_HOME%\.cache\uv"
set "UV_CREDENTIALS_DIR=%NERV_HOME%\.cache\uv-credentials"
"%NERV_HOME%\bin\uv.exe" run --offline --managed-python --no-project --python 3.12 "%NERV_HOME%\bootstrap\launch.py" %*
'@ | Set-Content -Encoding ascii "$nervRoot/bin/nerv.cmd"
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($userPath -split ';') -notcontains "$nervRoot\bin") { [Environment]::SetEnvironmentVariable('Path', "$nervRoot\bin;$userPath", 'User') }
Write-Host 'Open a new terminal and run nerv. Windows hardware support is experimental.'
Get-Command nerv -All -ErrorAction SilentlyContinue
