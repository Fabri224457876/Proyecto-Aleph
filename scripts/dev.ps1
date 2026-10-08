<#
.SYNOPSIS
    Levanta la demo de Aleph: base SQLite, administrador, caso ficticio y API en el puerto 8100.

.DESCRIPTION
    1. Crea la base SQLite en data\demo\aleph-demo.db (si no existe).
    2. Crea el administrador con el bootstrap, si la base todavía no tiene ninguno.
    3. Siembra el caso de demostración (ficticio) y los usuarios analista-demo y auditor-demo.
    4. Levanta la API en http://127.0.0.1:8100 (documentación en /docs).

    Las contraseñas se toman de ALEPH_ADMIN_PASSWORD y ALEPH_DEMO_PASSWORD. Si no están, el script
    las genera y las imprime al final. No se guardan en ningún archivo.

.PARAMETER Reiniciar
    Borra la base y los datos de la demo antes de sembrar.

.PARAMETER Puerto
    Puerto de la API. Por defecto 8100 (el 8101 queda para la consola web).

.EXAMPLE
    .\scripts\dev.ps1
.EXAMPLE
    .\scripts\dev.ps1 -Reiniciar
#>
param(
    [switch]$Reiniciar,
    [int]$Puerto = 8100
)

$ErrorActionPreference = "Stop"
# Consola en UTF-8 para que los textos en español se vean bien (solo en una consola real)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz

$Python = Join-Path $Raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "No hay entorno virtual en .venv. Creálo con: python -m venv .venv; .venv\Scripts\python -m pip install -e `"backend[dev]`""
}

# Antes de sembrar: si el puerto está ocupado, la API no podría levantar y la base quedaría a medias
$ocupadoPor = $null
try {
    $ocupadoPor = (Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction Stop | Select-Object -First 1).OwningProcess
} catch {
    $ocupadoPor = $null
}
if ($ocupadoPor) {
    throw "El puerto $Puerto ya lo usa el proceso $ocupadoPor. Cerralo o elegí otro con -Puerto."
}

function New-Secret {
    param([int]$Bytes = 18)
    $buffer = New-Object byte[] $Bytes
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($buffer)
    $rng.Dispose()
    # Solo caracteres seguros para copiar y pegar
    return ([Convert]::ToBase64String($buffer) -replace '[+/=]', 'x')
}

$DataDir = Join-Path $Raiz "data\demo"
$DbPath = Join-Path $DataDir "aleph-demo.db"
if ($Reiniciar -and (Test-Path $DataDir)) {
    Remove-Item -Recurse -Force $DataDir
    Write-Host "Se borraron los datos anteriores de la demo."
}
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$env:ALEPH_ENV = "dev"
$env:ALEPH_DATABASE_URL = "sqlite:///" + ($DbPath -replace '\\', '/')
$env:ALEPH_DATA_DIR = $DataDir
$env:PYTHONPATH = Join-Path $Raiz "backend"
$env:PYTHONUTF8 = "1"
if (-not $env:ALEPH_SECRET_KEY) {
    # Clave de firma de los tokens, propia de esta ejecución. Para una instalación, definila fija.
    $env:ALEPH_SECRET_KEY = New-Secret -Bytes 32
}
if (-not $env:ALEPH_ADMIN_USERNAME) { $env:ALEPH_ADMIN_USERNAME = "admin" }

$generated = @{}

# 1) Administrador (bootstrap), solo si la base todavía no tiene ninguno
$admins = [int]((& $Python -m aleph.demo.estado) | Select-Object -Last 1)
if ($admins -eq 0) {
    if (-not $env:ALEPH_ADMIN_PASSWORD) {
        $env:ALEPH_ADMIN_PASSWORD = New-Secret -Bytes 12
        $generated[$env:ALEPH_ADMIN_USERNAME] = $env:ALEPH_ADMIN_PASSWORD
    }
    Write-Host "Creando el administrador '$($env:ALEPH_ADMIN_USERNAME)' (bootstrap)..."
    & $Python -m aleph.api.bootstrap
    if ($LASTEXITCODE -ne 0) { throw "El bootstrap falló (código $LASTEXITCODE)." }
} else {
    Write-Host "La base ya tiene un administrador: no se crea otro."
}

# 2) Caso de demostración (ficticio) y usuarios de demo. Es idempotente.
& $Python -m aleph.demo.seed
if ($LASTEXITCODE -ne 0) { throw "El sembrador falló (código $LASTEXITCODE)." }

# 3) Resumen y API
Write-Host ""
if ($generated.Count -gt 0) {
    Write-Host "Contraseña generada por el script para el administrador '$($env:ALEPH_ADMIN_USERNAME)': $($generated[$env:ALEPH_ADMIN_USERNAME])"
}
Write-Host "API de Aleph: http://127.0.0.1:$Puerto (documentación: http://127.0.0.1:$Puerto/docs)"
Write-Host "Ctrl+C para detener."
& $Python -m uvicorn aleph.main:app --host 127.0.0.1 --port $Puerto
