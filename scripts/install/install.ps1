param([string]$Bundle = '', [string]$Target = '', [ValidateSet('auto','cpu','cu130')][string]$Compute = 'auto', [switch]$NoPath)
$ErrorActionPreference = 'Stop'
$nervRoot = if ($env:NERV_HOME) { $env:NERV_HOME } else { Join-Path $env:USERPROFILE '.nerv' }
$env:NERV_HOME = $nervRoot
$arch = [System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture.ToString()
if ($arch -ne 'X64') { throw 'This release requires a Windows x64 process. ARM and 32-bit Windows are not supported.' }
if ($Target -and $Target -ne 'windows-x86_64') { throw 'Target conflicts with this Windows process.' }
$Target = 'windows-x86_64'
Get-Command curl.exe -ErrorAction Stop | Out-Null
$releaseBase = if ($env:NERV_RELEASE_BASE) { $env:NERV_RELEASE_BASE } else { 'https://github.com/Yanshi-Robotics/nerv/releases/latest/download' }
New-Item -ItemType Directory -Force -Path "$nervRoot/bin", "$nervRoot/bootstrap" | Out-Null
function Get-Entry($name, $local) {
 if ($Bundle) { Copy-Item "$Bundle/$name" "$nervRoot/bootstrap/$local" }
 else {
  & curl.exe --fail --location --proto '=https' --proto-redir '=https' --retry 5 --connect-timeout 60 --max-time 180 "$releaseBase/$name" -o "$nervRoot/bootstrap/$local"
  if ($LASTEXITCODE -ne 0) { throw "Could not obtain $name. Rerun the installer to retry." }
 }
}
Get-Entry "seed-$Target.ps1" 'seed.ps1'
& "$nervRoot/bootstrap/seed.ps1"
Get-Entry 'bootstrap.py' 'install.py'
Get-Entry 'launch.py' 'launch.py'
Get-Entry 'release.json' 'release.json'
$manifestData = Get-Content "$nervRoot/bootstrap/release.json" -Raw | ConvertFrom-Json
foreach ($pair in @(@('bootstrap.py','install.py'), @('launch.py','launch.py'))) {
 if ((Get-FileHash "$nervRoot/bootstrap/$($pair[1])" -Algorithm SHA256).Hash.ToLower() -ne $manifestData.files.($pair[0]).sha256) { throw "Installer checksum mismatch: $($pair[0])" }
}
$python = (Get-Content "$nervRoot/bootstrap/python-path" -Raw).Trim()
$manifest = if ($Bundle) { Join-Path $Bundle 'release.json' } else { "$nervRoot/bootstrap/release.json" }
& $python "$nervRoot/bootstrap/install.py" --manifest $manifest --target $Target --compute $Compute
if ($LASTEXITCODE -ne 0) { throw 'NERV installation failed' }
@'
@echo off
if not defined NERV_HOME set "NERV_HOME=%USERPROFILE%\.nerv"
set /p NERV_BOOTSTRAP_PYTHON=<"%NERV_HOME%\bootstrap\python-path"
"%NERV_BOOTSTRAP_PYTHON%" "%NERV_HOME%\bootstrap\launch.py" %*
'@ | Set-Content -Encoding ascii "$nervRoot/bin/nerv.cmd"
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not $NoPath -and ($userPath -split ';') -notcontains "$nervRoot\bin") { [Environment]::SetEnvironmentVariable('Path', "$nervRoot\bin;$userPath", 'User') }
Write-Host 'Open a new terminal and run nerv. Windows hardware support is experimental.'
Get-Command nerv -All -ErrorAction SilentlyContinue
