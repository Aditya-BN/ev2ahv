#!/bin/bash
# Post-cutover / post-rollback check for the Windows test guest, over WinRM. Exit non-zero on failure.
set -a; . ~/.config/ev2ahv/win.env; set +a
cd "$(dirname "$0")/.." || exit 1
.venv/bin/python tools/winrm_run.py "${1:-10.12.136.161}" - <<'PS'
$cs = Get-CimInstance Win32_ComputerSystem
"platform: $($cs.Manufacturer) $($cs.Model)"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ev2ahv\guest-load.ps1 -Mode check
Get-Volume -DriveLetter C,F | ForEach-Object { "volume {0}: {1} {2} {3:N1} GiB" -f $_.DriveLetter, $_.FileSystem, $_.HealthStatus, ($_.Size/1GB) }
Get-Disk | ForEach-Object { "disk {0}: {1} {2} online={3} {4:N1} GiB" -f $_.Number, $_.FriendlyName, $_.PartitionStyle, (-not $_.IsOffline), ($_.Size/1GB) }
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' } | ForEach-Object { "ip: {0}/{1} on {2}" -f $_.IPAddress, $_.PrefixLength, $_.InterfaceAlias }
Get-NetAdapter | ForEach-Object { "nic: {0} {1} {2} {3}" -f $_.Name, $_.MacAddress, $_.Status, $_.InterfaceDescription }
Get-Service WinRM, TermService, LanmanServer | ForEach-Object { "service: {0} {1}" -f $_.Name, $_.Status }
$t = Get-ScheduledTask -TaskName 'ev2ahv-apply-network' -ErrorAction SilentlyContinue; "network task present: $([bool]$t)"
if (Test-Path C:\ProgramData\ev2ahv\apply-network.log) { Get-Content C:\ProgramData\ev2ahv\apply-network.log | Select-Object -Last 4 }
if ((& powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\ev2ahv\guest-load.ps1 -Mode check) -match 'FAILED') { exit 3 }
PS
