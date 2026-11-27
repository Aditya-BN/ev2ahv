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
                 It also makes vioscsi boot-start (service key Start=0 plus the virtio-scsi PCI IDs in the
                 CriticalDeviceDatabase) so the VM can boot straight from SCSI on AHV. The source keeps its own
                 controller; the keys it creates are recorded in C:\ev2ahv\scsi-boot.json.
  -Mode Remove   Removes the startup task and saved network config, and undoes the vioscsi boot-start keys
                 it created (not while a device uses vioscsi).
  -DryRun        With Prep, Cleanup or Remove: print what would be removed or uninstalled and change nothing.

  The startup task does nothing unless the machine is a Nutanix VM, so booting the source again after a
  rollback is safe. The last output line is "EV2AHV-JSON:" followed by the facts as JSON.
  Exit: 0 done, 1 something needs attention, 2 refused.
#>
[CmdletBinding()]
param(
    [ValidateSet('Check', 'Prep', 'Cleanup', 'Remove')][string]$Mode = 'Check',
    [string]$VirtioPath = '',
    [ValidateSet('scsi', 'pci')][string]$Bus = 'scsi',
    [switch]$InstallNgt,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = 'C:\ev2ahv'
$NetFile = Join-Path $Root 'network.json'
$ApplyScript = Join-Path $Root 'apply-network.ps1'
$TaskName = 'ev2ahv-apply-network'
$Facts = [ordered]@{ version = 2; mode = $Mode; time = (Get-Date).ToUniversalTime().ToString('o')
                     computer = $env:COMPUTERNAME; bus = $Bus; changes = @(); problems = @() }

function Say($m) { Write-Host $m }
# Remove-Item -Recurse only through this guard: the path must be a real directory (not a junction or symlink) whose
# full path ends in the expected leaf, and must not be a drive root, the Windows directory or a profile root.
function Invoke-OwnedTreeDelete([string]$Path, [string]$Leaf) {
    if ([string]::IsNullOrWhiteSpace($Path) -or [string]::IsNullOrWhiteSpace($Leaf)) { throw "refusing to delete: empty path or leaf" }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full.Length -lt 6 -or $full -match '^[A-Za-z]:$' -or (Split-Path $full -Leaf) -ne $Leaf) { throw "refusing to delete '$full': not an ev2ahv directory" }
    if ($full -ieq $env:SystemRoot -or $full -ieq $env:USERPROFILE -or $full -ieq $env:ProgramData -or $full -ieq $env:ProgramFiles) { throw "refusing to delete '$full'" }
    if (-not (Test-Path -LiteralPath $full -PathType Container)) { return }
    if ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "refusing to delete '$full': it is a junction or symlink" }
    Remove-Item -LiteralPath $full -Recurse -Force
}
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

# PCI IDs of the virtio-scsi controller AHV presents (legacy/transitional 1004 and modern 1048), as CriticalDeviceDatabase keys.
$CddRoot = 'HKLM:\SYSTEM\CurrentControlSet\Control\CriticalDeviceDatabase'
$ScsiCdd = @('pci#ven_1af4&dev_1004&subsys_00081af4&rev_00', 'pci#ven_1af4&dev_1004&subsys_00081af4', 'pci#ven_1af4&dev_1004',
             'pci#ven_1af4&dev_1048&subsys_11001af4&rev_01', 'pci#ven_1af4&dev_1048&subsys_11001af4', 'pci#ven_1af4&dev_1048')
$ScsiClassGuid = '{4D36E97B-E325-11CE-BFC1-08002BE10318}'
$ScsiMarker = Join-Path $Root 'scsi-boot.json'    # what Enable-ScsiBoot created, so Remove deletes only that
function ScsiCddKeys {
    foreach ($k in $ScsiCdd) {
        $path = Join-Path $CddRoot $k
        $svc = $null
        if (Test-Path -LiteralPath $path) { $svc = (Get-ItemProperty -LiteralPath $path -ErrorAction SilentlyContinue).Service }
        [ordered]@{ key = $k; present = [bool](Test-Path -LiteralPath $path); service = $svc }
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
    # vioscsi has no device on the source. It can still be bound at first boot on AHV when its PCI IDs are in the
    # CriticalDeviceDatabase (Windows then starts the boot-start driver for the new controller by itself).
    $critical = $false
    if ($name -eq 'vioscsi') { $critical = (@(ScsiCddKeys | Where-Object { $_.present -and $_.service -eq 'vioscsi' }).Count -eq $ScsiCdd.Count) }
    [ordered]@{ name = $name; sys = $sys; start = $start; staged = $staged; start_override = $override; bound = $bound; critical = $critical
                boot_ready = ($sys -and $start -eq 0 -and -not $override -and ($bound -or $critical)) }
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
    if ($state.staged -and (-not $bootStart -or ($state.sys -and $state.boot_ready))) { return }
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
    if ($name -notmatch '^[A-Za-z0-9]+$') { throw "bad driver service name '$name'" }
    if (Test-Path -LiteralPath "$svc\StartOverride") { Remove-Item -LiteralPath "$svc\StartOverride" -Recurse -Force; Change "removed $name StartOverride" }
}

# Makes vioscsi a boot-start driver on a source that has no virtio-scsi device, so the VM can boot straight from SCSI on AHV.
# It stages the driver, makes sure the service key exists with Start=0 and registers the controller's PCI IDs in the
# CriticalDeviceDatabase. The source keeps booting from its own controller; vioscsi just loads without a device.
# Every key this creates is recorded in scsi-boot.json so -Mode Remove can undo exactly that.
function Enable-ScsiBoot {
    InstallDriver 'vioscsi' $false
    $st = DriverState 'vioscsi'
    if (-not $st.staged) { Problem 'vioscsi is not staged, so it cannot be made boot-start'; return }
    $old = $null
    if (Test-Path -LiteralPath $ScsiMarker) { $old = Get-Content -LiteralPath $ScsiMarker -Raw | ConvertFrom-Json }
    $created = [ordered]@{ service_key = [bool]($old -and $old.service_key); sys_copied = [bool]($old -and $old.sys_copied); cdd = @(if ($old) { $old.cdd }) }
    $drv = Join-Path $env:SystemRoot 'System32\drivers\vioscsi.sys'
    if (-not (Test-Path -LiteralPath $drv)) {
        $repo = Get-ChildItem "$env:SystemRoot\System32\DriverStore\FileRepository" -Directory -Filter 'vioscsi.inf_*' -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName 'vioscsi.sys' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $repo) { Problem 'vioscsi.sys not found in the driver store'; return }
        Copy-Item -LiteralPath $repo -Destination $drv; $created.sys_copied = $true; Change 'copied vioscsi.sys to System32\drivers'
    }
    $svc = 'HKLM:\SYSTEM\CurrentControlSet\Services\vioscsi'
    if (-not (Test-Path -LiteralPath $svc)) {
        New-Item -Path $svc -Force | Out-Null
        New-ItemProperty -Path $svc -Name Type -Value 1 -PropertyType DWord | Out-Null
        New-ItemProperty -Path $svc -Name ErrorControl -Value 1 -PropertyType DWord | Out-Null
        New-ItemProperty -Path $svc -Name Group -Value 'SCSI miniport' -PropertyType String | Out-Null
        New-ItemProperty -Path $svc -Name ImagePath -Value 'system32\drivers\vioscsi.sys' -PropertyType ExpandString | Out-Null
        New-ItemProperty -Path $svc -Name DisplayName -Value 'Red Hat VirtIO SCSI pass-through controller' -PropertyType String | Out-Null
        New-Item -Path "$svc\Parameters\PnpInterface" -Force | Out-Null
        New-ItemProperty -Path "$svc\Parameters\PnpInterface" -Name '5' -Value 1 -PropertyType DWord | Out-Null
        $created.service_key = $true; Change 'created the vioscsi service key'
    }
    if ($null -eq (Get-ItemProperty -LiteralPath $svc).Start -or (Get-ItemProperty -LiteralPath $svc).Start -ne 0) {
        New-ItemProperty -Path $svc -Name Start -Value 0 -PropertyType DWord -Force | Out-Null; Change 'set vioscsi to boot-start'
    }
    if (Test-Path -LiteralPath "$svc\StartOverride") { Remove-Item -LiteralPath "$svc\StartOverride" -Recurse -Force; Change 'removed vioscsi StartOverride' }
    $added = 0
    foreach ($k in $ScsiCdd) {
        $path = Join-Path $CddRoot $k
        if (-not (Test-Path -LiteralPath $path)) { New-Item -Path $path -Force | Out-Null; $created.cdd += $k; $added++ }
        New-ItemProperty -Path $path -Name Service -Value 'vioscsi' -PropertyType String -Force | Out-Null
        New-ItemProperty -Path $path -Name ClassGUID -Value $ScsiClassGuid -PropertyType String -Force | Out-Null
    }
    if ($added) { Change "registered vioscsi in the CriticalDeviceDatabase ($added new key(s))" }
    ConvertTo-Json -InputObject $created -Depth 4 | Set-Content -LiteralPath $ScsiMarker -Encoding UTF8
}

# Undoes Enable-ScsiBoot: only the keys and the file it recorded as created, and only while no device uses vioscsi.
function Disable-ScsiBoot {
    if (-not (Test-Path -LiteralPath $ScsiMarker)) { Say 'no scsi-boot.json: nothing recorded to undo'; return }
    if ((DriverState 'vioscsi').bound) { Problem 'a device uses vioscsi (the VM boots from SCSI): leaving the driver as it is'; return }
    $m = Get-Content -LiteralPath $ScsiMarker -Raw | ConvertFrom-Json
    foreach ($k in @($m.cdd)) {
        if ($k -notin $ScsiCdd) { Problem "ignoring unexpected key '$k' in scsi-boot.json"; continue }
        $path = Join-Path $CddRoot $k
        if ($DryRun) { Say "[dry-run] would remove $path"; continue }
        Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue; Change "removed CriticalDeviceDatabase key $k"
    }
    $svc = 'HKLM:\SYSTEM\CurrentControlSet\Services\vioscsi'
    if ($m.service_key) {
        if ($DryRun) { Say "[dry-run] would remove $svc" } else { Remove-Item -LiteralPath $svc -Recurse -Force -ErrorAction SilentlyContinue; Change 'removed the vioscsi service key' }
    } elseif (Test-Path -LiteralPath $svc) {
        if ($DryRun) { Say '[dry-run] would set vioscsi back to demand start' } else { New-ItemProperty -Path $svc -Name Start -Value 3 -PropertyType DWord -Force | Out-Null; Change 'set vioscsi to demand start' }
    }
    if ($m.sys_copied) {
        $drv = Join-Path $env:SystemRoot 'System32\drivers\vioscsi.sys'
        if ($DryRun) { Say "[dry-run] would remove $drv" } else { Remove-Item -LiteralPath $drv -Force -ErrorAction SilentlyContinue; Change 'removed vioscsi.sys from System32\drivers' }
    }
    if (-not $DryRun) { Remove-Item -LiteralPath $ScsiMarker -Force -ErrorAction SilentlyContinue }
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
        if ($DryRun) { Say "[dry-run] would unregister task $TaskName and delete $NetFile and $ApplyScript"; Disable-ScsiBoot; Emit; exit 0 }
        Disable-ScsiBoot
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $NetFile, $ApplyScript -Force -ErrorAction SilentlyContinue
        Change 'removed the ev2ahv startup task and saved network config'
        Emit; exit 0
    }
    'Cleanup' {
        if (-not $Facts.on_ahv) { Problem 'refusing: this is not the migrated VM on AHV'; Emit; exit 2 }
        $ghosts = @(Get-PnpDevice -Class Net -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Unknown' -and $_.InstanceId -like 'PCI\*' })
        foreach ($g in $ghosts) {
            if ($DryRun) { Say "[dry-run] would remove ghost NIC $($g.FriendlyName) ($($g.InstanceId))"; continue }
            $out = & pnputil.exe /remove-device "$($g.InstanceId)" 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0) { Change "removed ghost NIC $($g.FriendlyName)" } else { Problem "could not remove $($g.FriendlyName): $($out.Trim())" }
        }
        $apps = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -match 'Stratus|everRun' })
        foreach ($app in $apps) {
            if ($DryRun) { Say "[dry-run] would uninstall $($app.DisplayName) ($($app.PSChildName))"; continue }
            if ($app.PSChildName -match '^\{[0-9A-Fa-f-]+\}$') {
                $p = Start-Process msiexec.exe -ArgumentList "/x $($app.PSChildName) /qn /norestart" -Wait -PassThru
                if ($p.ExitCode -in 0, 3010) { Change "uninstalled $($app.DisplayName)" } else { Problem "msiexec exit $($p.ExitCode) for $($app.DisplayName)" }
            } else { Problem "remove by hand: $($app.DisplayName)" }
        }
        if ($InstallNgt) {
            $cd = Get-Volume | Where-Object { $_.DriveType -eq 'CD-ROM' -and $_.DriveLetter -and (Test-Path "$($_.DriveLetter):\setup.exe") -and $_.FileSystemLabel -match 'NUTANIX' } | Select-Object -First 1
            if (-not $cd) { Problem 'NGT ISO is not attached' }
            elseif ($DryRun) { Say "[dry-run] would install Nutanix Guest Tools from $($cd.DriveLetter):" }
            else {
                $p = Start-Process "$($cd.DriveLetter):\setup.exe" -ArgumentList '/quiet ACCEPTEULA=yes /norestart' -Wait -PassThru
                if ($p.ExitCode -in 0, 3010) { Change "installed Nutanix Guest Tools (exit $($p.ExitCode))" } else { Problem "NGT setup exit $($p.ExitCode)" }
            }
        }
        $Facts.ghost_nics_left = @(Get-PnpDevice -Class Net -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Unknown' -and $_.InstanceId -like 'PCI\*' }).Count
        $Facts.stratus = @(Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match 'Stratus|everRun' } | ForEach-Object { $_.DisplayName })
        $Facts.ngt = [bool](Get-Service -Name 'Nutanix Guest Tools Agent' -ErrorAction SilentlyContinue)
        if ($DryRun) { Emit; exit 0 }
        $Facts | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Root 'cleanup.json') -Encoding UTF8
        Emit; if ($Facts.problems.Count) { exit 1 }; exit 0
    }
    'Prep' {
        if ($Facts.on_ahv) { Problem 'refusing: Prep runs on the source, and this is already on AHV'; Emit; exit 2 }
        if ($DryRun) {
            Say "[dry-run] would stage drivers from '$VirtioPath', make vioscsi boot-start (service key + $($ScsiCdd.Count) CriticalDeviceDatabase keys),"
            Say "[dry-run] set the SAN policy, save the network config and register the startup task $TaskName. Nothing was changed."
            $Facts.drivers = [ordered]@{ vioscsi = DriverState 'vioscsi'; viostor = DriverState 'viostor' }
            Emit; exit 0
        }
        InstallDriver 'viostor' ($Bus -eq 'pci')
        try { Enable-ScsiBoot } catch { Problem "could not make vioscsi boot-start: $($_.Exception.Message)" }   # lets the VM boot directly on SCSI on AHV
        InstallDriver 'netkvm' $false
        if ((SanPolicy) -notmatch 'Online All') { SetSanOnlineAll; Change 'SAN policy set to OnlineAll' }
        $net = CaptureNetwork
        ConvertTo-Json -InputObject @($net) -Depth 5 | Set-Content -Path $NetFile -Encoding UTF8
        Set-Content -Path $ApplyScript -Value $ApplyBody -Encoding UTF8
        if ([string]::IsNullOrWhiteSpace($env:ProgramData)) { throw 'ProgramData is not set; not touching the old task files' }
        $old = Join-Path $env:ProgramData 'ev2ahv'
        if (Test-Path -LiteralPath $old) { Invoke-OwnedTreeDelete $old 'ev2ahv'; Change 'removed the old ProgramData\ev2ahv task files' }
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
