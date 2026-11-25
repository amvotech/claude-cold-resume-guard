# Installs cold-resume-guard on Windows:  irm https://raw.githubusercontent.com/l2cache-app/claude-cold-resume-guard/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$RawUrl = "https://raw.githubusercontent.com/l2cache-app/claude-cold-resume-guard/main/guard.py"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\cold-resume-guard"

Write-Host "==> Installing claude-cold-resume-guard..."

# Python is required; the py launcher is preferred because "python" may be the Microsoft Store stub.
$Python = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Python = "py -3" }
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { $Python = "python" }
}
if (-not $Python) {
    Write-Host "Python 3.8 or newer is required. Install it with:" -ForegroundColor Yellow
    Write-Host "  winget install --id Python.Python.3.13 -e" -ForegroundColor Cyan
    Write-Host "then open a new terminal and run this installer again."
    return
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Invoke-WebRequest -Uri $RawUrl -OutFile (Join-Path $InstallDir "guard.py") -UseBasicParsing

# Launchers: one per command name, so claude-guard / codex-guard pick their agent from the name.
foreach ($name in @("cold-resume-guard", "claude-guard", "codex-guard")) {
    $shim = "@echo off`r`n$Python `"%~dp0guard.py`" %*`r`n"
    if ($name -ne "cold-resume-guard") {
        $agent = $name.Split("-")[0]
        $shim = "@echo off`r`n$Python `"%~dp0guard.py`" --agent $agent %*`r`n"
    }
    Set-Content -Path (Join-Path $InstallDir "$name.cmd") -Value $shim -Encoding ASCII -NoNewline
}

$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not (($UserPath -split ";") -contains $InstallDir)) {
    $NewPath = if ($UserPath) { "$UserPath;$InstallDir" } else { $InstallDir }
    [Environment]::SetEnvironmentVariable("Path", $NewPath, "User")
    Write-Host "==> Added $InstallDir to your user PATH"
}

Write-Host "==> Installed to $InstallDir" -ForegroundColor Green
Write-Host "Next: open a new terminal and run" -NoNewline
Write-Host "  cold-resume-guard --setup" -ForegroundColor Cyan
