param([string] $Name = 'agentmem')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run scripts/setup.ps1 first.' }
$codex = Get-Command codex -ErrorAction Stop
$data = Join-Path $root '.agentmem-data'
$existing = & $codex.Source mcp get $Name 2>$null
if ($LASTEXITCODE -eq 0) {
    $summary = $existing -join "`n"
    if ($summary.Contains($pythonExe) -and $summary.Contains('-m agentmem.server')) {
        Write-Host "Codex MCP '$Name' already points to this Agent Memory installation; leaving its existing config unchanged."
        exit 0
    }
    throw "Codex MCP '$Name' already exists with a different command. It was left unchanged; choose another name or review that exact entry before replacing it."
}
& $codex.Source mcp add $Name --env "AGENTMEM_DATA_DIR=$data" -- $pythonExe -m agentmem.server
if ($LASTEXITCODE -ne 0) { throw "codex mcp add failed with exit code $LASTEXITCODE" }
Write-Host "Registered local stdio MCP '$Name'. Restart or open a new Codex session to load it."
