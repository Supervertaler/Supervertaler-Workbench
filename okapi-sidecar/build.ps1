# ────────────────────────────────────────────────────────────
# Build the Okapi Sidecar fat JAR (Windows PowerShell)
#
# Prerequisites: Java 17+ and Maven 3.8+
#
# Usage:
#   .\build.ps1              Build the JAR
#   .\build.ps1 -Run         Build and run locally
#   .\build.ps1 -Docker      Build Docker image
#   .\build.ps1 -JLink       Build JAR + minimal JRE bundle
# ────────────────────────────────────────────────────────────
param(
    [switch]$Run,
    [switch]$Docker,
    [switch]$JLink
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot

$JAR_NAME = "okapi-sidecar"
$VERSION  = "0.1.9"
$PORT     = if ($env:PORT) { $env:PORT } else { "8090" }

function Build-Jar {
    Write-Host "Building $JAR_NAME..." -ForegroundColor Cyan
    mvn package -DskipTests -B
    if ($LASTEXITCODE -ne 0) { throw "Maven build failed" }
    Write-Host "Built: target\$JAR_NAME-$VERSION.jar" -ForegroundColor Green
}

function Run-Local {
    Build-Jar
    Write-Host "Starting sidecar on port $PORT..." -ForegroundColor Cyan
    java -jar "target\$JAR_NAME-$VERSION.jar" "--port=$PORT"
}

function Build-Docker {
    Write-Host "Building Docker image..." -ForegroundColor Cyan
    docker build -t supervertaler-okapi-sidecar:latest .
    Write-Host "Docker image built: supervertaler-okapi-sidecar:latest" -ForegroundColor Green
    Write-Host ""
    Write-Host "Run with:"
    Write-Host "  docker run -p 8090:8090 supervertaler-okapi-sidecar:latest"
}

function Build-JLink {
    Build-Jar
    Write-Host ""
    Write-Host "Creating minimal JRE bundle with jlink..." -ForegroundColor Cyan

    if (-not $env:JAVA_HOME) {
        throw "JAVA_HOME not set. Needed for jlink."
    }

    $jlink = Join-Path $env:JAVA_HOME "bin\jlink.exe"
    if (-not (Test-Path $jlink)) {
        throw "jlink not found at $jlink. Make sure you have a full JDK installed."
    }

    $modules = "java.base,java.xml,java.logging,java.desktop,java.naming,java.sql,java.management,jdk.crypto.ec"
    $outputDir = "target\jre-bundle"

    if (Test-Path $outputDir) { Remove-Item $outputDir -Recurse -Force }

    & $jlink `
        --add-modules $modules `
        --strip-debug `
        --no-man-pages `
        --no-header-files `
        --compress=2 `
        --output $outputDir

    if ($LASTEXITCODE -ne 0) { throw "jlink failed" }

    $size = (Get-ChildItem $outputDir -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
    Write-Host "Minimal JRE created: $outputDir ($([math]::Round($size, 1)) MB)" -ForegroundColor Green
    Write-Host ""
    Write-Host "To distribute:"
    Write-Host "  1. Copy target\$JAR_NAME-$VERSION.jar -> okapi-sidecar\okapi-sidecar.jar"
    Write-Host "  2. Copy $outputDir -> okapi-sidecar\jre\"
    Write-Host "  3. Run: okapi-sidecar\jre\bin\java.exe -jar okapi-sidecar\okapi-sidecar.jar"
}

# ── Main ─────────────────────────────────────────────────
try {
    if ($Run)    { Run-Local }
    elseif ($Docker) { Build-Docker }
    elseif ($JLink)  { Build-JLink }
    else         { Build-Jar }
} finally {
    Pop-Location
}
