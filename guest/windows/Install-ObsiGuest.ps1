#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'

$installDir = Join-Path $env:ProgramData 'OBSI'
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item -Force `
    (Join-Path $PSScriptRoot 'Expand-ObsiSystemDisk.ps1') `
    (Join-Path $installDir 'Expand-ObsiSystemDisk.ps1')

powercfg.exe /hibernate on
if ($LASTEXITCODE -ne 0) {
    throw 'Windows hibernation could not be enabled.'
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f `
        (Join-Path $installDir 'Expand-ObsiSystemDisk.ps1'))
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
)
$principal = New-ScheduledTaskPrincipal `
    -UserId 'SYSTEM' `
    -LogonType ServiceAccount `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -AllowStartIfOnBatteries

Register-ScheduledTask `
    -TaskName 'OBSI Expand System Disk' `
    -Action $action `
    -Trigger $triggers `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

$qga = Get-Service -Name 'qemu-ga' -ErrorAction SilentlyContinue
if (-not $qga) {
    Write-Warning 'QEMU Guest Agent is not installed. Install it from virtio-win before Alt+F12 can hibernate this workspace.'
} else {
    Set-Service -Name 'qemu-ga' -StartupType Automatic
    Start-Service -Name 'qemu-ga'
}

Write-Host 'OBSI guest integration installed.'
