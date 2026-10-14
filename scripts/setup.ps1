$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = (Get-Command py -ErrorAction SilentlyContinue).Source
if ($python) { & $python -3.13 -m venv (Join-Path $root '.venv') }
else {
    $python = (Get-Command python -ErrorAction Stop).Source
    & $python -m venv (Join-Path $root '.venv')
}
$venvPython = Join-Path $root '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host "Installed Agent Memory CLI and MCP dependencies in $root\.venv"
