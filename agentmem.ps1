param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $AgentmemArgs
)
$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $scriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Agent Memory environment is missing. Run: $scriptRoot\scripts\setup.ps1"
}
if (-not $env:AGENTMEM_DATA_DIR) {
    $env:AGENTMEM_DATA_DIR = Join-Path $scriptRoot '.agentmem-data'
}
$env:PYTHONPATH = Join-Path $scriptRoot 'src'
& $pythonExe -m agentmem.cli @AgentmemArgs
exit $LASTEXITCODE
