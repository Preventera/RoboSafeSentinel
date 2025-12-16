<#
.SYNOPSIS
    Lance RoboSafe Sentinel

.DESCRIPTION
    Script de lancement pour Windows PowerShell

.PARAMETER Simulate
    Lance en mode simulation (défaut)

.PARAMETER Production
    Lance avec la config production

.PARAMETER Port
    Port de l'API (défaut: 8080)

.EXAMPLE
    .\run.ps1
    Lance en mode simulation sur port 8080

.EXAMPLE
    .\run.ps1 -Production -Port 9000
    Lance en mode production sur port 9000
#>

param(
    [switch]$Simulate = $true,
    [switch]$Production,
    [int]$Port = 8080,
    [string]$CellId = "WELD-MIG-001"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   RoboSafe Sentinel - Demarrage" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Activer le venv si présent
$venvPath = Join-Path $PSScriptRoot "venv\Scripts\Activate.ps1"
if (Test-Path $venvPath) {
    Write-Host "Activation de l'environnement virtuel..." -ForegroundColor Yellow
    . $venvPath
}

# Déterminer le mode
$mode = "--simulate"
if ($Production) {
    $mode = "--config config/production.yaml"
    Write-Host "Mode: PRODUCTION" -ForegroundColor Red
} else {
    Write-Host "Mode: SIMULATION" -ForegroundColor Green
}

Write-Host "Cellule: $CellId" -ForegroundColor White
Write-Host "Port: $Port" -ForegroundColor White
Write-Host ""
Write-Host "Dashboard: http://localhost:$Port/static/dashboard.html" -ForegroundColor Cyan
Write-Host "API Docs:  http://localhost:$Port/docs" -ForegroundColor Cyan
Write-Host "Metrics:   http://localhost:$Port/metrics" -ForegroundColor Cyan
Write-Host ""
Write-Host "Appuyez sur Ctrl+C pour arreter" -ForegroundColor Yellow
Write-Host ""

# Lancer
try {
    python -m robosafe.integration $mode --port $Port --cell-id $CellId
}
catch {
    Write-Host "Erreur: $_" -ForegroundColor Red
    exit 1
}
