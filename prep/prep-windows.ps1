<#
.SYNOPSIS
  ev2ahv Windows guest prep. Run as Administrator inside the source VM.

.DESCRIPTION
  -Mode Check  (default) Read-only report.
  -Mode Apply  Sets SAN policy to OnlineAll, exports static IP config per MAC, and installs a
               one-shot startup task that re-applies it on AHV.
  -Mode Post   Run on the migrated VM on AHV: removes the Stratus guest agent.
  -Mode Remove Removes the startup task and saved config from this guest.

  The startup task does nothing unless the machine is a Nutanix VM, so it is harmless if the
  source VM is booted again after a rollback.

  Exit: 0 ready, 1 warnings, 2 blocked.
#>
[CmdletBinding()]
param([ValidateSet('Check','Apply','Post','Remove')][string]$Mode = 'Check')

$ErrorActionPreference = 'Stop'
$Root = Join-Path $env:ProgramData 'ev2ahv'
$NetFile = Join-Path $Root 'network.json'
$ApplyScript = Join-Path $Root 'apply-network.ps1'
$TaskName = 'ev2ahv-apply-network'
$script:Warn = 0; $script:Block = 0

function Ok($m)    { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Warn($m)  { Write-Host "[WARN] $m" -ForegroundColor Yellow; $script:Warn++ }
function Stop2($m) { Write-Host "[STOP] $m" -ForegroundColor Red; $script:Block++ }
function Info($m)  { Write-Host "[info] $m" }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this from an elevated PowerShell.'; exit 2
}

function Get-SanPolicy {
    $f = Join-Path $env:TEMP 'ev2ahv-san.txt'
    Set-Content -Path $f -Value 'san' -Encoding ASCII
    $out = & diskpart.exe /s $f
    Remove-Item $f -Force
    $line = $out | Where-Object { $_ -match 'SAN Policy' } | Select-Object -First 1
    if ($line) { return ($line -split ':')[-1].Trim() }
    return 'unknown'
}

function Set-SanOnlineAll {
    $f = Join-Path $env:TEMP 'ev2ahv-san.txt'
    Set-Content -Path $f -Value 'san policy=OnlineAll' -Encoding ASCII
    & diskpart.exe /s $f | Out-Null
    Remove-Item $f -Force
}

function Get-DriverState($name) {
    $svc = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
    $inStore = [bool](Get-WindowsDriver -Online -ErrorAction SilentlyContinue |
        Where-Object { $_.OriginalFileName -match "\\$name\.inf$" } | Select-Object -First 1)
    $sys = Test-Path (Join-Path $env:SystemRoot "System32\drivers\$name.sys")
    $start = $null
    if (Test-Path $svc) { $start = (Get-ItemProperty $svc).Start }
    [pscustomobject]@{ Name = $name; InStore = $inStore; SysFile = $sys; Start = $start; BootReady = ($sys -and $start -eq 0) }
}

function Export-Network {
    $items = foreach ($a in Get-NetAdapter -Physical | Where-Object { $_.Status -ne 'Disabled' }) {
        $ipif = Get-NetIPInterface -InterfaceIndex $a.ifIndex -AddressFamily IPv4
        $ips = @(Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.PrefixOrigin -eq 'Manual' } | ForEach-Object { @{ ip = $_.IPAddress; prefix = $_.PrefixLength } })
        $gw = @(Get-NetRoute -InterfaceIndex $a.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            ForEach-Object { $_.NextHop })
        $dns = @((Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4).ServerAddresses)
        [pscustomobject]@{
            mac = ($a.MacAddress -replace '-', ':').ToLower(); name = $a.Name
            dhcp = ($ipif.Dhcp -eq 'Enabled'); addresses = $ips; gateway = $gw; dns = $dns
        }
    }
    return @($items)
}

$ApplyBody = @'
# ev2ahv one-shot: re-applies static IP config by MAC, on AHV only, then removes its own task.
$Root = Join-Path $env:ProgramData 'ev2ahv'
$Log = Join-Path $Root 'apply-network.log'
function L($m) { Add-Content -Path $Log -Value ("{0:u} {1}" -f (Get-Date).ToUniversalTime(), $m) }
try {
    $cs = Get-CimInstance Win32_ComputerSystem
    if ($cs.Manufacturer -notmatch 'Nutanix') { L "not on AHV ($($cs.Manufacturer)), nothing to do"; exit 0 }
    $wanted = @(Get-Content (Join-Path $Root 'network.json') -Raw | ConvertFrom-Json)
    $deadline = (Get-Date).AddMinutes(3)
    do {
        $adapters = @(Get-NetAdapter -Physical)
        $missing = @($wanted | Where-Object { $m = $_.mac; -not ($adapters | Where-Object { ($_.MacAddress -replace '-',':').ToLower() -eq $m }) })
        if ($missing.Count -eq 0) { break }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    if ($missing.Count) { L "adapters not found: $($missing.mac -join ', '). Will retry next boot"; exit 1 }
    Set-Content -Path (Join-Path $env:TEMP 'ev2ahv-online.txt') -Value "san policy=OnlineAll" -Encoding ASCII
    & diskpart.exe /s (Join-Path $env:TEMP 'ev2ahv-online.txt') | Out-Null
    Get-Disk | Where-Object { $_.IsOffline } | Set-Disk -IsOffline $false
    Get-Disk | Where-Object { $_.IsReadOnly } | Set-Disk -IsReadOnly $false
    foreach ($n in $wanted) {
        $a = $adapters | Where-Object { ($_.MacAddress -replace '-',':').ToLower() -eq $n.mac } | Select-Object -First 1
        if ($n.dhcp) { L "$($n.mac) uses DHCP, leaving it"; continue }
        Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
        Get-NetRoute -InterfaceIndex $a.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
        Set-NetIPInterface -InterfaceIndex $a.ifIndex -Dhcp Disabled
        $first = $true
        foreach ($ip in @($n.addresses)) {
            $p = @{ InterfaceIndex = $a.ifIndex; IPAddress = $ip.ip; PrefixLength = [int]$ip.prefix }
            if ($first -and @($n.gateway).Count) { $p.DefaultGateway = @($n.gateway)[0] }
            New-NetIPAddress @p | Out-Null
            $first = $false
        }
        if (@($n.dns).Count) { Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses @($n.dns) }
        L "applied $($n.mac): $(@($n.addresses | ForEach-Object { $_.ip }) -join ', ')"
    }
    Unregister-ScheduledTask -TaskName 'ev2ahv-apply-network' -Confirm:$false
    L 'done, task removed'
} catch { L "error: $_"; exit 1 }
'@

switch ($Mode) {
    'Remove' {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        if (Test-Path $Root) { Remove-Item $Root -Recurse -Force }
        Ok 'Removed the ev2ahv task and saved config.'; exit 0
    }
    'Post' {
        $cs = Get-CimInstance Win32_ComputerSystem
        if ($cs.Manufacturer -notmatch 'Nutanix') { Write-Host 'Refusing: this is not the migrated VM on AHV.'; exit 2 }
        $apps = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                  'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'Stratus|everRun' })
        if (-not $apps.Count) { Ok 'No Stratus guest software found.' }
        foreach ($app in $apps) {
            Info "Removing $($app.DisplayName)"
            if ($app.PSChildName -match '^\{[0-9A-Fa-f-]+\}$') {
                $p = Start-Process msiexec.exe -ArgumentList "/x $($app.PSChildName) /qn /norestart" -Wait -PassThru
                if ($p.ExitCode -in 0, 3010) { Ok "Removed $($app.DisplayName)" } else { Warn "msiexec exit $($p.ExitCode) for $($app.DisplayName)" }
            } else { Warn "Remove by hand: $($app.DisplayName) ($($app.UninstallString))" }
        }
        Info 'Nutanix Guest Tools are optional. Install from Prism if you need them.'
        exit $(if ($script:Warn) { 1 } else { 0 })
    }
}

Write-Host "ev2ahv Windows prep ($Mode): $env:COMPUTERNAME, $((Get-CimInstance Win32_OperatingSystem).Caption)"

Write-Host "`n--- Firmware"
try { $null = Confirm-SecureBootUEFI; Info 'Firmware: UEFI. Set target.firmware: uefi' }
catch [System.PlatformNotSupportedException] { Info 'Firmware: BIOS. Set target.firmware: bios' }
catch { Info 'Firmware: UEFI (Secure Boot state unreadable). Set target.firmware: uefi' }

Write-Host "`n--- VirtIO drivers"
$scsi = Get-DriverState 'vioscsi'; $blk = Get-DriverState 'viostor'; $net = Get-DriverState 'netkvm'
foreach ($d in $scsi, $blk, $net) { Info ("{0}: file={1} store={2} start={3}" -f $d.Name, $d.SysFile, $d.InStore, $d.Start) }
if ($scsi.BootReady) { Ok 'vioscsi is boot-start. Use disk_bus: scsi' }
elseif ($blk.BootReady) { Warn 'vioscsi is not boot-start, viostor is. Use disk_bus: pci, convert to SCSI later' }
else { Stop2 'No boot-start VirtIO storage driver. Install Nutanix VirtIO in this guest before cutover, or it will blue-screen (INACCESSIBLE_BOOT_DEVICE)' }
if ($net.SysFile -or $net.InStore) { Ok 'netkvm is present' } else { Warn 'netkvm is missing. The NIC will not work on AHV until Nutanix VirtIO is installed' }

Write-Host "`n--- SAN policy"
$san = Get-SanPolicy
if ($san -match 'Online All') { Ok "SAN policy: $san" }
elseif ($Mode -eq 'Apply') { Set-SanOnlineAll; Ok "SAN policy changed from '$san' to OnlineAll" }
else { Warn "SAN policy is '$san'. Data disks may come up offline on AHV. Run -Mode Apply" }

Write-Host "`n--- Network"
$net = Export-Network
foreach ($n in $net) {
    $ips = @($n.addresses | ForEach-Object { "$($_.ip)/$($_.prefix)" }) -join ', '
    Info ("{0} {1} dhcp={2} ip={3} gw={4} dns={5}" -f $n.name, $n.mac, $n.dhcp, $ips, (@($n.gateway) -join ','), (@($n.dns) -join ','))
}

Write-Host "`n--- Stratus guest software"
$st = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'Stratus|everRun' })
if ($st.Count) { Info "Found: $(($st.DisplayName) -join ', '). Remove on AHV with -Mode Post" } else { Ok 'None found' }

if ($Mode -eq 'Apply') {
    Write-Host "`n--- Installing the one-shot startup task"
    New-Item -ItemType Directory -Force -Path $Root | Out-Null
    ConvertTo-Json -InputObject @($net) -Depth 5 | Set-Content -Path $NetFile -Encoding UTF8
    Set-Content -Path $ApplyScript -Value $ApplyBody -Encoding UTF8
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ApplyScript`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal2 = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal2 -Force | Out-Null
    Ok "Task '$TaskName' installed. It only acts on AHV. Network saved to $NetFile"
}

Write-Host "`nSummary: $script:Block blocking, $script:Warn warnings"
if ($script:Block) { exit 2 }
if ($script:Warn) { exit 1 }
exit 0
