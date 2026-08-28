$ErrorActionPreference = 'Stop'
$fixtureRoot = 'C:\Users\27059\AppData\Local\Temp\novelforge-browser-current-20260825-01'
$null = New-Item -ItemType Directory -Force -Path $fixtureRoot
$env:NOVELFORGE_ROOT = $fixtureRoot
$env:NOVELFORGE_DISABLE_STUDIO_WORKER = '1'
$pythonPath = (Get-Command python).Source
$stdoutPath = Join-Path $fixtureRoot 'server.stdout.log'
$stderrPath = Join-Path $fixtureRoot 'server.stderr.log'
$server = Start-Process -FilePath $pythonPath `
    -ArgumentList 'run.py serve --host 127.0.0.1 --port 8787' `
    -WorkingDirectory 'C:\CODEX\新小说' `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($server.HasExited) {
        break
    }
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8787/api/health' -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # The server may still be importing the application.
    }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) {
    if ($server.HasExited) {
        Write-Output "temporary Studio server exited with code $($server.ExitCode)"
    }
    Write-Output '--- server stderr ---'
    Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue
    Write-Output '--- server stdout ---'
    Get-Content -LiteralPath $stdoutPath -ErrorAction SilentlyContinue
    throw 'temporary Studio server did not become ready'
}
Write-Output (ConvertTo-Json @{ root = $fixtureRoot; pid = $server.Id; health = $response.Content })
