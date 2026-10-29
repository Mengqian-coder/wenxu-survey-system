param(
    [ValidateSet('help', 'start', 'db-start', 'db-stop', 'db-status', 'db-init', 'backend', 'frontend', 'test', 'build')]
    [string]$Action = 'help',
    [ValidateSet('all', 'backend', 'frontend', 'e2e')]
    [string]$Suite = 'all',
    [string]$Mongod
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Action -eq 'help') {
    Write-Host 'Usage: .\dev.ps1 <action> [-Suite all|backend|frontend|e2e] [-Mongod <path>]'
    Write-Host 'Actions: start, db-start, db-stop, db-status, db-init, backend, frontend, test, build'
    Write-Host 'Development: MongoDB 27019 / survey_system_v2; API 8001; frontend 5175.'
    Write-Host 'Quick start: .\dev.ps1 start (ensures MongoDB is ready, serves the complete app on http://127.0.0.1:8001).'
    Write-Host 'Frontend development: run backend and frontend in separate terminals; press Ctrl+C to stop each.'
    exit 0
}

$codeRoot = $PSScriptRoot
$backendRoot = Join-Path $codeRoot 'backend'
$frontendRoot = Join-Path $codeRoot 'frontend'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
$mongoScript = Join-Path $backendRoot 'scripts\dev_mongo.py'
$vite = Join-Path $frontendRoot 'node_modules\vite\bin\vite.js'
$playwright = Join-Path $frontendRoot 'node_modules\@playwright\test\cli.js'
$mongoUri = 'mongodb://127.0.0.1:27019/?replicaSet=rs0'
$database = 'survey_system_v2'

function Require-File {
    param([string]$Path, [string]$Hint)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Missing dependency: $Path. $Hint"
    }
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable"
    }
}

function Require-Node {
    $command = Get-Command 'node.exe' -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw 'Node.js is missing from PATH. Install Node.js before continuing.'
    }
    Require-File $vite 'Run npm.cmd --prefix frontend ci in this code directory.'
    return $command.Source
}

function Resolve-Mongod {
    $candidate = $Mongod
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $candidate = [Environment]::GetEnvironmentVariable('MONGOD_BIN', 'Process')
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $command = Get-Command 'mongod.exe' -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            $candidate = $command.Source
        }
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw 'MongoDB Server was not found. Pass -Mongod <path> or set MONGOD_BIN.'
    }
    Require-File $candidate 'Pass the path to the installed mongod executable.'
    return (Resolve-Path -LiteralPath $candidate).ProviderPath
}

function Test-LoopbackPort {
    param([int]$Port)
    $connection = New-Object System.Net.Sockets.TcpClient
    try {
        return ($connection.ConnectAsync('127.0.0.1', $Port).Wait(800) -and $connection.Connected)
    } catch {
        return $false
    } finally {
        $connection.Dispose()
    }
}

function Test-WenxuBackend {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        if ($health.status -ne 'ok') { return $false }
        $schema = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/openapi.json' -TimeoutSec 3
        # Use the ASCII brand and route contract; Windows PowerShell 5 may
        # misdecode non-ASCII JSON text when a response omits a charset.
        if ($schema.info.title -notlike 'MongoDB *') { return $false }
        foreach ($path in @('/api/surveys', '/api/questions', '/api/question-statistics')) {
            if ($null -eq $schema.paths.PSObject.Properties[$path]) { return $false }
        }
        return $true
    } catch {
        return $false
    }
}

function Start-ProjectDatabase {
    # The Python manager verifies PID/dbPath/port/replica set before reusing a listener.
    $arguments = @($mongoScript, 'start', '--port', '27019')
    if (-not (Test-LoopbackPort 27019) -or -not [string]::IsNullOrWhiteSpace($Mongod)) {
        $env:MONGOD_BIN = Resolve-Mongod
        $arguments += @('--mongod', $env:MONGOD_BIN)
    }
    Write-Host 'Checking project MongoDB on 127.0.0.1:27019...'
    Invoke-Checked $python $arguments
}

function Start-ProjectBackend {
    Require-File (Join-Path $backendRoot '.env') 'Create the local configuration with setup_env.py first.'
    if (Test-LoopbackPort 8001) {
        if (Test-WenxuBackend) {
            Write-Host '问序已经在运行，无需重复启动。' -ForegroundColor Green
            Write-Host '请直接在浏览器打开：http://127.0.0.1:8001'
            Write-Host '本次没有新启动服务；可以关闭当前窗口，原来的服务会继续运行。'
            return
        }
        throw 'Port 8001 is already in use. If the project is already running, open http://127.0.0.1:8001; otherwise stop the conflicting service in its own terminal.'
    }
    Start-ProjectDatabase
    $env:MONGODB_URI = $mongoUri
    $env:MONGODB_DATABASE = $database
    Write-Host 'Project website and API: http://127.0.0.1:8001 (wait for Application startup complete; Ctrl+C to stop)'
    Invoke-Checked $python @('-m', 'uvicorn', 'app.main:app', '--app-dir', $backendRoot, '--host', '127.0.0.1', '--port', '8001', '--no-access-log')
}

function Require-Backend {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 3
        if ($health.status -ne 'ok') { throw 'Unexpected health response.' }
    } catch {
        throw 'The backend on 8001 is not ready. Run .\dev.ps1 backend in another terminal first, or use .\dev.ps1 start for the complete app on 8001.'
    }
}

$originalLocation = Get-Location
$environmentNames = @('TEMP', 'TMP', 'MONGOD_BIN', 'MONGODB_URI', 'MONGODB_DATABASE', 'API_TARGET')
$originalEnvironment = @{}
foreach ($name in $environmentNames) {
    $originalEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

try {
    Set-Location -LiteralPath $codeRoot
    $taskTemp = Join-Path $codeRoot 'output\tmp'
    [System.IO.Directory]::CreateDirectory($taskTemp) | Out-Null
    $env:TEMP = $taskTemp
    $env:TMP = $taskTemp

    if ($Action -like 'db-*' -or $Action -in @('start', 'backend', 'test')) {
        Require-File $python 'Create backend/.venv and install backend/requirements-lock.txt.'
        if ($Action -like 'db-*') {
            Invoke-Checked $python @('-c', 'import pymongo')
        } elseif ($Action -in @('start', 'backend')) {
            Invoke-Checked $python @('-c', 'import uvicorn, fastapi, pymongo, pydantic_settings')
        } else {
            Invoke-Checked $python @('-c', 'import pytest, uvicorn, fastapi, pymongo, httpx')
        }
    }

    switch ($Action) {
        'start' {
            Require-File (Join-Path $frontendRoot 'dist\index.html') 'Run .\dev.ps1 build to create the production frontend first.'
            Start-ProjectBackend
        }
        'db-start' {
            Start-ProjectDatabase
        }
        'db-stop' {
            Invoke-Checked $python @($mongoScript, 'stop', '--port', '27019')
        }
        'db-status' {
            Invoke-Checked $python @($mongoScript, 'status', '--port', '27019')
        }
        'db-init' {
            Require-File (Join-Path $backendRoot '.env') 'Create the local configuration with setup_env.py first.'
            Invoke-Checked $python @($mongoScript, 'init', '--port', '27019', '--database', $database)
        }
        'backend' {
            Start-ProjectBackend
        }
        'frontend' {
            $node = Require-Node
            Require-Backend
            $env:API_TARGET = 'http://127.0.0.1:8001'
            Set-Location -LiteralPath $frontendRoot
            Write-Host 'Development frontend: http://127.0.0.1:5175 (Ctrl+C to stop)'
            Invoke-Checked $node @($vite, '--host', '127.0.0.1', '--port', '5175', '--strictPort')
        }
        'test' {
            if ($Suite -ne 'frontend') {
                $env:MONGOD_BIN = Resolve-Mongod
            }
            if ($Suite -ne 'backend') {
                $null = Require-Node
            }
            if ($Suite -eq 'all' -or $Suite -eq 'e2e') {
                Require-File $playwright 'Run npm.cmd --prefix frontend ci in this code directory.'
            }
            Invoke-Checked $python @((Join-Path $backendRoot 'scripts\run_tests.py'), '--suite', $Suite)
        }
        'build' {
            $null = Require-Node
            $npm = Get-Command 'npm.cmd' -ErrorAction Stop
            Invoke-Checked $npm.Source @('--prefix', $frontendRoot, 'run', 'build')
        }
    }
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $originalEnvironment[$name], 'Process')
    }
    Set-Location -LiteralPath $originalLocation.Path
}
