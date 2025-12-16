# RoboSafe Sentinel - Windows Setup Script
# Run in PowerShell as Administrator

param(
    [switch]$Dev,
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"

Write-Host "=== RoboSafe Sentinel Setup ===" -ForegroundColor Cyan

# Check Python version
Write-Host "`nChecking Python..." -ForegroundColor Yellow
$pythonVersion = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Python not found. Please install Python 3.10+ from python.org" -ForegroundColor Red
    exit 1
}
Write-Host "Found: $pythonVersion" -ForegroundColor Green

# Create virtual environment
if (-not $SkipVenv) {
    Write-Host "`nCreating virtual environment..." -ForegroundColor Yellow
    if (Test-Path "venv") {
        Write-Host "Virtual environment already exists, skipping..." -ForegroundColor Gray
    } else {
        python -m venv venv
        Write-Host "Virtual environment created." -ForegroundColor Green
    }
    
    Write-Host "Activating virtual environment..." -ForegroundColor Yellow
    & .\venv\Scripts\Activate.ps1
}

# Upgrade pip
Write-Host "`nUpgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# Install package
Write-Host "`nInstalling RoboSafe Sentinel..." -ForegroundColor Yellow
if ($Dev) {
    Write-Host "Installing with dev dependencies..." -ForegroundColor Gray
    pip install -e ".[dev]"
} else {
    pip install -e .
}

# Copy config
Write-Host "`nSetting up configuration..." -ForegroundColor Yellow
if (-not (Test-Path "config\config.yaml")) {
    Copy-Item "config\config.example.yaml" "config\config.yaml"
    Write-Host "Created config\config.yaml from example." -ForegroundColor Green
    Write-Host "Please edit config\config.yaml with your settings." -ForegroundColor Yellow
} else {
    Write-Host "config\config.yaml already exists." -ForegroundColor Gray
}

# Create log directory
if (-not (Test-Path "data\logs")) {
    New-Item -ItemType Directory -Path "data\logs" -Force | Out-Null
}

# Verify installation
Write-Host "`nVerifying installation..." -ForegroundColor Yellow
python -c "import robosafe; print(f'RoboSafe version: {robosafe.__version__}')"

Write-Host "`n=== Setup Complete ===" -ForegroundColor Green
Write-Host @"

Next steps:
1. Edit config\config.yaml with your equipment settings
2. Run in simulation mode:
   python -m robosafe.main --mode simulation

3. Run in production mode:
   python -m robosafe.main --config config\config.yaml

4. Run tests:
   pytest tests\ -v

For VS Code:
- Open this folder in VS Code
- Install recommended extensions
- Select Python interpreter: .\venv\Scripts\python.exe

"@ -ForegroundColor Cyan
