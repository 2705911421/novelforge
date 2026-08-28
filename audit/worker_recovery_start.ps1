$ErrorActionPreference = 'Stop'
$fixtureRoot = 'C:\Users\27059\AppData\Local\Temp\novelforge-worker-recovery-f900c84e19994d35ad316571308399dd'
$env:NOVELFORGE_ROOT = $fixtureRoot
Remove-Item Env:NOVELFORGE_DISABLE_STUDIO_WORKER -ErrorAction SilentlyContinue
$stdoutPath = Join-Path $fixtureRoot 'server.stdout.log'
$stderrPath = Join-Path $fixtureRoot 'server.stderr.log'
$pythonPath = (Get-Command python).Source
$server = Start-Process -FilePath $pythonPath -ArgumentList 'run.py serve --host 127.0.0.1 --port 8789' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:8789/api/health' -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { Get-Content -LiteralPath $stderrPath -ErrorAction SilentlyContinue; throw 'temporary Worker-enabled Studio server did not become ready' }
Write-Output (ConvertTo-Json @{ root = $fixtureRoot; pid = $server.Id; health = $response.Content })
