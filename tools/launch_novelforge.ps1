param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$url = "http://127.0.0.1:$Port"

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    [System.Windows.Forms.MessageBox]::Show(
        "Python not found. Install Python 3.11+ or create a project .venv.",
        'NovelForge startup failed', 'OK', 'Error') | Out-Null
    exit 1
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'NovelForge Launcher'
$form.Size = New-Object System.Drawing.Size(460, 245)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $true

$title = New-Object System.Windows.Forms.Label
$title.Text = 'NovelForge Studio'
$title.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 16, [System.Drawing.FontStyle]::Bold)
$title.Location = New-Object System.Drawing.Point(24, 18)
$title.AutoSize = $true
$form.Controls.Add($title)

$status = New-Object System.Windows.Forms.Label
$status.Text = 'Preparing to start...'
$status.Location = New-Object System.Drawing.Point(27, 62)
$status.AutoSize = $true
$form.Controls.Add($status)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Style = 'Marquee'
$progress.Location = New-Object System.Drawing.Point(27, 88)
$progress.Size = New-Object System.Drawing.Size(400, 18)
$progress.Visible = $false
$form.Controls.Add($progress)

$start = New-Object System.Windows.Forms.Button
$start.Text = 'Start Studio'
$start.Location = New-Object System.Drawing.Point(27, 125)
$start.Size = New-Object System.Drawing.Size(180, 36)
$form.Controls.Add($start)

$stop = New-Object System.Windows.Forms.Button
$stop.Text = 'Stop service'
$stop.Location = New-Object System.Drawing.Point(220, 125)
$stop.Size = New-Object System.Drawing.Size(100, 36)
$stop.Enabled = $false
$form.Controls.Add($stop)

$open = New-Object System.Windows.Forms.Button
$open.Text = 'Open browser'
$open.Location = New-Object System.Drawing.Point(333, 125)
$open.Size = New-Object System.Drawing.Size(94, 36)
$open.Enabled = $false
$form.Controls.Add($open)

$hint = New-Object System.Windows.Forms.Label
$hint.Text = "URL: $url`nProject: $ProjectRoot"
$hint.ForeColor = [System.Drawing.Color]::DimGray
$hint.Location = New-Object System.Drawing.Point(27, 178)
$hint.AutoSize = $true
$form.Controls.Add($hint)

$server = $null
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500

$setRunning = {
    $status.Text = "Studio is running: $url"
    $status.ForeColor = [System.Drawing.Color]::DarkGreen
    $progress.Visible = $false
    $start.Enabled = $false
    $stop.Enabled = $true
    $open.Enabled = $true
}

$timer.Add_Tick({
    if ($server -and $server.HasExited) {
        $timer.Stop()
        $status.Text = 'Studio stopped. Check the server output for errors.'
        $status.ForeColor = [System.Drawing.Color]::DarkRed
        $progress.Visible = $false
        $start.Enabled = $true
        $stop.Enabled = $false
        $open.Enabled = $false
        return
    }
    try {
        # Studio exposes GET / but not HEAD /; use the same method as a browser.
        $response = Invoke-WebRequest -Uri $url -Method Get -TimeoutSec 1 -UseBasicParsing
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            & $setRunning
            $timer.Stop()
            Start-Process $url
        }
    } catch { }
})

$start.Add_Click({
    if ($server -and -not $server.HasExited) { return }
    $status.Text = 'Starting Studio...'
    $status.ForeColor = [System.Drawing.Color]::Black
    $progress.Visible = $true
    $start.Enabled = $false
    $server = Start-Process -FilePath $python -ArgumentList @('run.py', 'serve', '--host', '127.0.0.1', '--port', "$Port") -WorkingDirectory $ProjectRoot -PassThru
    $timer.Start()
})

$open.Add_Click({ Start-Process $url })

$stop.Add_Click({
    if ($server -and -not $server.HasExited) {
        $server.Kill()
        $server.WaitForExit(3000)
    }
    $timer.Stop()
    $status.Text = 'Service stopped.'
    $status.ForeColor = [System.Drawing.Color]::Black
    $progress.Visible = $false
    $start.Enabled = $true
    $stop.Enabled = $false
    $open.Enabled = $false
})

$form.Add_FormClosing({
    if ($server -and -not $server.HasExited) {
        $server.Kill()
        $server.WaitForExit(3000)
    }
})

$form.Add_Shown({ $start.PerformClick() })
[void]$form.ShowDialog()
