<#
.SYNOPSIS
  Sets up the eCallisto weather log receiver on this Windows PC.

.DESCRIPTION
  - Generates a self-signed TLS cert/key (if not already present) via openssl.
  - Generates a random shared-secret token (if not already present).
  - Prints the certificate's SHA-256 fingerprint -- copy this and the token
    into pi/config.json on the Raspberry Pi (see pi/config.example.json).
  - Adds a Windows Firewall rule (Private profile only, inbound TCP 9443).
  - Registers a Scheduled Task that runs receiver.py at logon and restarts
    it automatically if it exits.

  Run this yourself in an elevated PowerShell prompt -- it changes firewall
  and scheduled-task state, which this assistant will not do on your behalf.

.NOTES
  Requires: Python on PATH, openssl on PATH (ships with Git for Windows).
#>

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CertPath  = Join-Path $ScriptDir "cert.pem"
$KeyPath   = Join-Path $ScriptDir "key.pem"
$TokenPath = Join-Path $ScriptDir "token.txt"
$FpPath    = Join-Path $ScriptDir "fingerprint.txt"
$OutDir    = Join-Path $ScriptDir "incoming_logs"
$Port      = 9443
$TaskName  = "eCallisto Weather Log Receiver"

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not (Test-Path $CertPath) -or -not (Test-Path $KeyPath)) {
    Write-Host "Generating self-signed TLS certificate..."
    & openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes `
        -keyout $KeyPath -out $CertPath -subj "/CN=ecallisto-receiver"
    if ($LASTEXITCODE -ne 0) { throw "openssl certificate generation failed" }
} else {
    Write-Host "Certificate already exists, reusing it."
}

if (-not (Test-Path $TokenPath)) {
    Write-Host "Generating shared-secret token..."
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    ($bytes | ForEach-Object { $_.ToString("x2") }) -join "" | Set-Content -NoNewline -Path $TokenPath -Encoding ascii
} else {
    Write-Host "Token already exists, reusing it."
}

# SHA-256 fingerprint of the DER-encoded certificate (matches what sender.py computes).
$certObj = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($CertPath)
$sha256 = [System.Security.Cryptography.SHA256]::Create()
$fp = ($sha256.ComputeHash($certObj.RawData) | ForEach-Object { $_.ToString("x2") }) -join ""
Set-Content -NoNewline -Path $FpPath -Value $fp -Encoding ascii

Write-Host ""
Write-Host "=== Copy these into pi/config.json on the Raspberry Pi ==="
Write-Host "host:        <this PC's LAN IP>"
Write-Host "port:        $Port"
Write-Host "token:       $(Get-Content $TokenPath)"
Write-Host "fingerprint: $fp"
Write-Host "============================================================"
Write-Host ""

Write-Host "Adding firewall rule (Private profile, inbound TCP $Port)..."
Remove-NetFirewallRule -DisplayName "eCallisto Weather Receiver" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "eCallisto Weather Receiver" -Direction Inbound `
    -Protocol TCP -LocalPort $Port -Profile Private -Action Allow | Out-Null

$pythonPath = (Get-Command python).Source
$receiverScript = Join-Path $ScriptDir "receiver.py"
$argList = "`"$receiverScript`" --port $Port --cert `"$CertPath`" --key `"$KeyPath`" --token-file `"$TokenPath`" --outdir `"$OutDir`""

Write-Host "Registering scheduled task '$TaskName' (runs at logon, restarts on failure)..."
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $argList -WorkingDirectory $ScriptDir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Limited | Out-Null

Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Done. Receiver is running and will start automatically at logon."
Write-Host "Incoming rows will land in: $OutDir"
