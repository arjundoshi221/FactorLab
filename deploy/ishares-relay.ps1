<#
.SYNOPSIS
Relay the iShares IWV holdings file from this machine to the VPS.

.DESCRIPTION
iShares' CDN (Akamai) refuses the VPS's datacenter address with HTTP 403, but serves a
residential connection. This script downloads latest-holdings.csv here, checks it is a
current, complete Russell 3000 holdings file, and installs it atomically as
/etc/factorlab/constituents/iwv-holdings.csv. universe-us reads it through the read-only
constituents mount (deploy/us-universe.yaml, providers.ishares ... path). A file that
fails a check is never shipped; the VPS keeps the last good copy and the provider's
max_age_days rejects it once it is a week old.

With -Register, it copies itself to %LOCALAPPDATA%\FactorLab and registers the
"FactorLab iShares relay" scheduled task (08:30 and 20:30 daily, catching up after a
missed start). Without it, it relays once. The log is
%LOCALAPPDATA%\FactorLab\ishares-relay.log.
#>
[CmdletBinding()]
param(
    [switch]$Register,
    [string]$VpsHost = "ubuntu@145.239.75.163",
    [string]$KeyPath = (Join-Path $HOME ".ssh\factorlab_vps_ed25519")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Url = "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/latest-holdings.csv"
$Target = "/etc/factorlab/constituents/iwv-holdings.csv"
$MinimumEquities = 2400
$MaxAgeDays = 6  # one day inside the provider's max_age_days so a relayed file is never already stale
$Home_ = Join-Path $env:LOCALAPPDATA "FactorLab"
$Log = Join-Path $Home_ "ishares-relay.log"
$Ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
$Scp = Join-Path $env:SystemRoot "System32\OpenSSH\scp.exe"
$SshOptions = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-i", $KeyPath)

New-Item -ItemType Directory -Force $Home_ | Out-Null

function Write-Log([string]$Message) {
    $line = "{0:u} {1}" -f (Get-Date).ToUniversalTime(), $Message
    Add-Content -Path $Log -Value $line -Encoding utf8
    Write-Host $line
}

if ($Register) {
    $installed = Join-Path $Home_ "ishares-relay.ps1"
    Copy-Item -Force $PSCommandPath $installed
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$installed`"")
    $triggers = @((New-ScheduledTaskTrigger -Daily -At "08:30"), (New-ScheduledTaskTrigger -Daily -At "20:30"))
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
    Register-ScheduledTask -TaskName "FactorLab iShares relay" -Force -Action $action `
        -Trigger $triggers -Settings $settings `
        -Description "Relay iShares IWV holdings to the FactorLab VPS (deploy/ishares-relay.ps1)" | Out-Null
    Write-Log "registered scheduled task running $installed"
    return
}

$work = Join-Path $Home_ "iwv-holdings.download.csv"
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $work -UseBasicParsing -TimeoutSec 60 `
        -UserAgent "Mozilla/5.0 (FactorLab research)"

    $lines = Get-Content -Path $work -Encoding utf8
    if ($lines[0] -notmatch "^\W*iShares Russell 3000 ETF") { throw "not the IWV holdings file: '$($lines[0])'" }
    $dated = $lines | Where-Object { $_ -like "Fund Holdings as of,*" } | Select-Object -First 1
    if (-not $dated) { throw "no 'Fund Holdings as of' line" }
    $asOf = [datetime]::ParseExact($dated.Split(",", 2)[1].Trim('"', " "), "MMM d, yyyy",
        [Globalization.CultureInfo]::InvariantCulture)
    $age = ((Get-Date).Date - $asOf.Date).Days
    if ($age -lt -1 -or $age -gt $MaxAgeDays) { throw "holdings dated $($asOf.ToString('yyyy-MM-dd')) are $age days old" }
    $equities = @($lines | Where-Object { $_ -like '*,"Equity",*' }).Count
    if ($equities -lt $MinimumEquities) { throw "$equities equity lines is below $MinimumEquities" }

    $staging = "/tmp/iwv-holdings.$([guid]::NewGuid().ToString('N')).csv"
    & $Scp @SshOptions -q $work "${VpsHost}:$staging"
    if ($LASTEXITCODE) { throw "scp exited $LASTEXITCODE" }
    $install = "set -e; trap 'rm -f $staging' EXIT; " +
        "sudo -n install -o root -g root -m 0644 $staging $Target.new; sudo -n mv -f $Target.new $Target"
    & $Ssh @SshOptions $VpsHost $install
    if ($LASTEXITCODE) { throw "remote install exited $LASTEXITCODE" }
    Write-Log "relayed holdings as of $($asOf.ToString('yyyy-MM-dd')): $equities equity lines"
}
catch {
    Write-Log "FAILED: $($_.Exception.Message)"
    exit 1
}
finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $work
}
