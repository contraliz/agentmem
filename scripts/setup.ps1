param(
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if ($PythonExe) {
    $command = Get-Command $PythonExe -ErrorAction Stop
    $python = $command.Source
    $pythonArgs = @()
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = (Get-Command py).Source
    $pythonArgs = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = (Get-Command python).Source
    $pythonArgs = @()
} else {
    throw 'Python 3.11 or newer is required. Install Python or rerun with -PythonExe <path-to-python.exe>.'
}

& $python @pythonArgs -c "import sys; print(f'Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}'); sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'AgentMem requires Python 3.11 or newer, matching pyproject.toml. Choose a compatible interpreter with -PythonExe <path-to-python.exe>.'
}

$venv = Join-Path $root '.venv'
& $python @pythonArgs -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed. Check the selected Python installation and permissions.' }

$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { $venvPython = Join-Path $venv 'bin\python' }
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip in the project environment.' }
& $venvPython -m pip install -e $root
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Confirm network access and retry setup.' }
Write-Host "Installed Agent Memory CLI and MCP dependencies in $venv"
