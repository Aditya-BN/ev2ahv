<#
.SYNOPSIS
  ev2ahv Windows guest prep and cleanup. Run as Administrator. Idempotent.

.DESCRIPTION
  -Mode Check    Read-only. Prints the facts ev2ahv grades as PASS/WARN/FAIL.
  -Mode Prep     On the source: installs VirtIO drivers from -VirtioPath and makes the boot driver for
                 -Bus boot-start, sets SAN policy OnlineAll, saves the IP config of every adapter by MAC,
                 and registers a one-shot startup task that re-applies it on AHV. Writes C:\ev2ahv\prep.json.
  -Mode Cleanup  On the migrated VM on AHV: removes ghost NICs, uninstalls Stratus guest software, and
                 installs Nutanix Guest Tools with -InstallNgt when the NGT ISO is attached.
                 Writes C:\ev2ahv\cleanup.json.
  -Mode Remove   Removes the startup task and saved network config.

  The startup task does nothing unless the machine is a Nutanix VM, so booting the source again after a
  rollback is safe. The last output line is "EV2AHV-JSON:" followed by the facts as JSON.
  Exit: 0 done, 1 something needs attention, 2 refused.
#>
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Prep', 'Cleanup', 'Remove')][string]$Mode = 'Check',
    [string]$VirtioPath = '',
    [ValidateSet('scsi', 'pci')][string]$Bus = 'scsi',
    [switch]$InstallNgt
)

$ErrorActionPreference = 'Stop'
$Root = 'C:\ev2ahv'
$NetFile = Join-Path $Root 'network.json'
$ApplyScript = Join-Path $Root 'apply-network.ps1'
$TaskName = 'ev2ahv-apply-network'
$Facts = [ordered]@{ version = 2; mode = $Mode; time = (Get-Date).ToUniversalTime().ToString('o')
                     computer = $env:COMPUTERNAME; bus = $Bus; changes = @(); problems = @() }

function Say($m) { Write-Host $m }
function Change($m) { $script:Facts.changes += $m; Write-Host "[CHG ] $m" }
function Problem($m) { $script:Facts.problems += $m; Write-Host "[PROB] $m" }
function Emit {
    Write-Host ('EV2AHV-JSON:' + ($script:Facts | ConvertTo-Json -Depth 6 -Compress))
}

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this from an elevated PowerShell.'; exit 2
}
New-Item -ItemType Directory -Force -Path $Root | Out-Null

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$Facts.os = $os.Caption
$Facts.build = [int]$os.BuildNumber
$Facts.manufacturer = $cs.Manufacturer
$Facts.on_ahv = [bool]($cs.Manufacturer -match 'Nutanix')
try { $null = Confirm-SecureBootUEFI; $Facts.firmware = 'uefi' }
catch [System.PlatformNotSupportedException] { $Facts.firmware = 'bios' }
catch { $Facts.firmware = 'uefi' }

# ---------------------------------------------------------------- drivers
function OsFolder([int]$build) {
    switch ($build) {
        { $_ -ge 26100 } { return '2k25' }
        { $_ -ge 20348 } { return '2k22' }
        { $_ -ge 17763 } { return '2k19' }
        { $_ -ge 14393 } { return '2k16' }
        { $_ -ge 9600 }  { return '2k12R2' }
        default          { return '2k12' }
    }
}

function DriverState($name) {
    $svc = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
    $sys = Test-Path "$env:SystemRoot\System32\drivers\$name.sys"
    $start = $null
    if (Test-Path $svc) { $start = (Get-ItemProperty $svc).Start }
    $override = Test-Path "$svc\StartOverride"
    $staged = [bool](Get-ChildItem "$env:SystemRoot\System32\DriverStore\FileRepository" -Directory -Filter "$name.inf_*" -ErrorAction SilentlyContinue)
    # A storage driver can boot Windows on a new bus only if a present device already uses it.
    # Boot-start alone is not enough: tested on Server 2022, it ends in the recovery environment.
    $bound = [bool](Get-CimInstance Win32_PnPEntity -Filter "Service = '$name'" -ErrorAction SilentlyContinue)
    [ordered]@{ name = $name; sys = $sys; start = $start; staged = $staged; start_override = $override; bound = $bound
                boot_ready = ($sys -and $start -eq 0 -and -not $override -and $bound) }
}

function FindInf($name) {
    if (-not $VirtioPath -or -not (Test-Path $VirtioPath)) { return $null }
    $folder = OsFolder $Facts.build
    $all = @(Get-ChildItem -Path $VirtioPath -Recurse -Filter "$name.inf" -ErrorAction SilentlyContinue)
    $best = $all | Where-Object { $_.FullName -match "\\$folder\\amd64\\" } | Select-Object -First 1
    if (-not $best -and $all.Count -eq 1) { $best = $all[0] }
    if ($best) { return $best.FullName }
    return $null
}

function InstallDriver($name, [bool]$bootStart) {
    $state = DriverState $name
    if ($state.staged -and $state.sys -and (-not $bootStart -or $state.boot_ready)) { return }
    $inf = FindInf $name
    if (-not $inf) { Problem "$name.inf for $(OsFolder $Facts.build)/amd64 not found under '$VirtioPath'"; return }
    if (-not $state.staged) {
        $out = & pnputil.exe /add-driver "$inf" /install 2>&1 | Out-String
        if ($LASTEXITCODE -notin 0, 259, 3010) { Problem "pnputil failed for ${name}: $($out.Trim())"; return }
        Change "staged $name from $inf"
    }
    if (-not $bootStart) { return }
    $svc = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
    if ((Test-Path $svc) -and (Get-ItemProperty $svc).Start -ne 0 -and (DriverState $name).bound) { Set-ItemProperty $svc -Name Start -Value 0; Change "set $name to boot-start" }
    if (Test-Path "$svc\StartOverride") { Remove-Item "$svc\StartOverride" -Recurse -Force; Change "removed $name StartOverride" }
}

# ---------------------------------------------------------------- SAN policy
function SanPolicy {
    $f = Join-Path $env:TEMP 'ev2ahv-san.txt'
    Set-Content -Path $f -Value 'san' -Encoding ASCII
    $line = & diskpart.exe /s $f | Where-Object { $_ -match 'SAN Policy' } | Select-Object -First 1
    Remove-Item $f -Force
    if ($line) { return ($line -split ':')[-1].Trim() }
    return 'unknown'
}

function SetSanOnlineAll {
    $f = Join-Path $env:TEMP 'ev2ahv-san.txt'
    Set-Content -Path $f -Value 'san policy=OnlineAll' -Encoding ASCII
    & diskpart.exe /s $f | Out-Null
    Remove-Item $f -Force
}

# ---------------------------------------------------------------- network capture
function CaptureNetwork {
    $items = foreach ($cfg in Get-CimInstance Win32_NetworkAdapterConfiguration -Filter 'IPEnabled = TRUE') {
        $ad = Get-NetAdapter -InterfaceIndex $cfg.InterfaceIndex -ErrorAction SilentlyContinue
        if (-not $ad -or $ad.Virtual) { continue }
        $addrs = @(Get-NetIPAddress -InterfaceIndex $cfg.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.PrefixOrigin -eq 'Manual' } | ForEach-Object { [ordered]@{ ip = $_.IPAddress; prefix = [int]$_.PrefixLength } })
        [ordered]@{
            mac = ($cfg.MACAddress -replace '-', ':').ToLower(); name = $ad.Name; status = "$($ad.Status)"
            dhcp = [bool]$cfg.DHCPEnabled; addresses = $addrs
            masks = @($cfg.IPSubnet | Where-Object { $_ -match '^\d+\.' })
            gateways = @($cfg.DefaultIPGateway | Where-Object { $_ -match '^\d+\.' })
            dns = @($cfg.DNSServerSearchOrder | Where-Object { $_ -match '^\d+\.' })
            wins = @(@($cfg.WINSPrimaryServer, $cfg.WINSSecondaryServer) | Where-Object { $_ -match '^\d+\.' })
        }
    }
    return @($items)
}

$ApplyBody = @'
# ev2ahv one-shot: on AHV only, re-applies the saved IP config to the adapter with the same MAC
# (or the only connected adapter), brings disks online and writable, logs, then removes itself.
$Root = 'C:\ev2ahv'
$Log = Join-Path $Root 'apply-network.log'
function L($m) { Add-Content -Path $Log -Value ("{0:u} {1}" -f (Get-Date).ToUniversalTime(), $m) }
try {
    if ((Get-CimInstance Win32_ComputerSystem).Manufacturer -notmatch 'Nutanix') { L 'not on AHV, nothing to do'; exit 0 }
    Get-Disk | Where-Object IsOffline | ForEach-Object { Set-Disk -Number $_.Number -IsOffline $false; L "disk $($_.Number) brought online" }
    Get-Disk | Where-Object IsReadOnly | ForEach-Object { Set-Disk -Number $_.Number -IsReadOnly $false; L "disk $($_.Number) made writable" }
    $wanted = @(Get-Content (Join-Path $Root 'network.json') -Raw | ConvertFrom-Json)
    $deadline = (Get-Date).AddMinutes(3)
    do {
        $adapters = @(Get-NetAdapter -Physical)
        $missing = @($wanted | Where-Object { $m = $_.mac; -not ($adapters | Where-Object { ($_.MacAddress -replace '-', ':').ToLower() -eq $m }) })
        if (-not $missing.Count) { break }
        Start-Sleep -Seconds 5
    } while ((Get-Date) -lt $deadline)
    foreach ($n in $wanted) {
        $a = $adapters | Where-Object { ($_.MacAddress -replace '-', ':').ToLower() -eq $n.mac } | Select-Object -First 1
        if (-not $a) {
            $up = @($adapters | Where-Object Status -eq 'Up')
            if ($wanted.Count -eq 1 -and $up.Count -eq 1) { $a = $up[0]; L "MAC $($n.mac) not found, using the only connected adapter $($a.Name)" }
            else { L "MAC $($n.mac) not found and no single connected adapter. Will retry next boot"; exit 1 }
        }
        if ($n.dhcp) { L "$($n.mac) uses DHCP, left as is"; continue }
        Get-NetIPAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
        Get-NetRoute -InterfaceIndex $a.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
        Set-NetIPInterface -InterfaceIndex $a.ifIndex -Dhcp Disabled
        $first = $true
        foreach ($ip in @($n.addresses)) {
            $p = @{ InterfaceIndex = $a.ifIndex; IPAddress = $ip.ip; PrefixLength = [int]$ip.prefix }
            if ($first -and @($n.gateways).Count) { $p.DefaultGateway = @($n.gateways)[0] }
            New-NetIPAddress @p | Out-Null
            $first = $false
        }
        if (@($n.dns).Count) { Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses @($n.dns) }
        if (@($n.wins).Count) {
            $cfg = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "InterfaceIndex = $($a.ifIndex)"
            $w = @($n.wins) + @('', '')
            Invoke-CimMethod -InputObject $cfg -MethodName SetWINSServer -Arguments @{ WINSPrimaryServer = $w[0]; WINSSecondaryServer = $w[1] } | Out-Null
        }
        L "applied $($n.mac) to $($a.Name): $(@($n.addresses | ForEach-Object { $_.ip }) -join ', ')"
    }
    Unregister-ScheduledTask -TaskName 'ev2ahv-apply-network' -Confirm:$false
    L 'done, task removed'
} catch { L "error: $_"; exit 1 }
'@

function TaskState {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $saved = @()
    if (Test-Path $NetFile) { $saved = @((Get-Content $NetFile -Raw | ConvertFrom-Json) | ForEach-Object { $_.mac }) }
    [ordered]@{ registered = [bool]$t; script = (Test-Path $ApplyScript); saved_macs = $saved }
}

# ---------------------------------------------------------------- modes
switch ($Mode) {
    'Remove' {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item $NetFile, $ApplyScript -Force -ErrorAction SilentlyContinue
        Change 'removed the ev2ahv startup task and saved network config'
        Emit; exit 0
    }
    'Cleanup' {
        if (-not $Facts.on_ahv) { Problem 'refusing: this is not the migrated VM on AHV'; Emit; exit 2 }
        $ghosts = @(Get-PnpDevice -Class Net -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Unknown' -and $_.InstanceId -like 'PCI\*' })
        foreach ($g in $ghosts) {
            $out = & pnputil.exe /remove-device "$($g.InstanceId)" 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) { Change "removed ghost NIC $($g.FriendlyName)" } else { Problem "could not remove $($g.FriendlyName): $($out.Trim())" }
        }
        $apps = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'Stratus|everRun' })
        foreach ($app in $apps) {
            if ($app.PSChildName -match '^\{[0-9A-Fa-f-]+\}$') {
                $p = Start-Process msiexec.exe -ArgumentList "/x $($app.PSChildName) /qn /norestart" -Wait -PassThru
                if ($p.ExitCode -in 0, 3010) { Change "uninstalled $($app.DisplayName)" } else { Problem "msiexec exit $($p.ExitCode) for $($app.DisplayName)" }
            } else { Problem "remove by hand: $($app.DisplayName)" }
        }
        if ($InstallNgt) {
            $cd = Get-Volume | Where-Object { $_.DriveType -eq 'CD-ROM' -and $_.DriveLetter -and (Test-Path "$($_.DriveLetter):\setup.exe") -and $_.FileSystemLabel -match 'NUTANIX' } | Select-Object -First 1
            if (-not $cd) { Problem 'NGT ISO is not attached' }
            else {
                $p = Start-Process "$($cd.DriveLetter):\setup.exe" -ArgumentList '/quiet ACCEPTEULA=yes /norestart' -Wait -PassThru
                if ($p.ExitCode -in 0, 3010) { Change "installed Nutanix Guest Tools (exit $($p.ExitCode))" } else { Problem "NGT setup exit $($p.ExitCode)" }
            }
        }
        $Facts.ghost_nics_left = @(Get-PnpDevice -Class Net -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Unknown' -and $_.InstanceId -like 'PCI\*' }).Count
        $Facts.stratus = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match 'Stratus|everRun' } | ForEach-Object { $_.DisplayName })
        $Facts.ngt = [bool](Get-Service -Name 'Nutanix Guest Tools Agent' -ErrorAction SilentlyContinue)
        $Facts | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Root 'cleanup.json') -Encoding UTF8
        Emit; if ($Facts.problems.Count) { exit 1 }; exit 0
    }
    'Prep' {
        if ($Facts.on_ahv) { Problem 'refusing: Prep runs on the source, and this is already on AHV'; Emit; exit 2 }
        InstallDriver 'viostor' ($Bus -eq 'pci')
        InstallDriver 'vioscsi' ($Bus -eq 'scsi')   # staged now, so Windows installs it by itself when a SCSI controller appears on AHV
        InstallDriver 'netkvm' $false
        if ((SanPolicy) -notmatch 'Online All') { SetSanOnlineAll; Change 'SAN policy set to OnlineAll' }
        $net = CaptureNetwork
        ConvertTo-Json -InputObject @($net) -Depth 5 | Set-Content -Path $NetFile -Encoding UTF8
        Set-Content -Path $ApplyScript -Value $ApplyBody -Encoding UTF8
        $old = Join-Path $env:ProgramData 'ev2ahv'
        if (Test-Path $old) { Remove-Item $old -Recurse -Force; Change 'removed the old ProgramData\ev2ahv task files' }
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ApplyScript`""
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $who = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $who -Force | Out-Null
        Change "startup task $TaskName registered for $(@($net).Count) adapter(s)"
    }
}

# Check and Prep both end with the current facts.
$Facts.drivers = [ordered]@{ vioscsi = DriverState 'vioscsi'; viostor = DriverState 'viostor'; netkvm = DriverState 'netkvm' }
$Facts.san_policy = SanPolicy
$Facts.adapters = @(CaptureNetwork)
$Facts.task = TaskState
$Facts.stratus = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'Stratus|everRun' } | ForEach-Object { $_.DisplayName })
$Facts.prepared_at = $null
if (Test-Path (Join-Path $Root 'prep.json')) { $Facts.prepared_at = (Get-Content (Join-Path $Root 'prep.json') -Raw | ConvertFrom-Json).time }
if ($Mode -eq 'Prep') {
    $Facts.prepared_at = $Facts.time
    $Facts | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Root 'prep.json') -Encoding UTF8
}
foreach ($d in $Facts.drivers.Values) { Say ("[info] {0}: sys={1} staged={2} start={3} boot_ready={4}" -f $d.name, $d.sys, $d.staged, $d.start, $d.boot_ready) }
Say "[info] SAN policy: $($Facts.san_policy)"
foreach ($n in $Facts.adapters) { Say ("[info] {0} {1} dhcp={2} ip={3}" -f $n.name, $n.mac, $n.dhcp, (@($n.addresses | ForEach-Object { "$($_.ip)/$($_.prefix)" }) -join ',')) }
Emit
if ($Facts.problems.Count) { exit 1 }
exit 0
