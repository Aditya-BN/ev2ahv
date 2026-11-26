# Stage B write load inside a Windows source guest. Run elevated.
# start: registers a SYSTEM task that writes files to F: and C:   stop: ends it and writes manifests
# check: verifies the manifests (used on the source after rollback, and on AHV after cutover)
param([ValidateSet('start','loop','stop','check')][string]$Mode)
$Dirs = 'F:\ev2ahv-data', 'C:\ev2ahv-root'
$Task = 'ev2ahv-load'
function Manifest($d) { Join-Path $d 'MANIFEST.txt' }
switch ($Mode) {
  'start' {
    $Dirs | ForEach-Object { New-Item -ItemType Directory -Force -Path $_ | Out-Null }
    $a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Mode loop"
    $p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $Task -Action $a -Principal $p -Force | Out-Null
    Start-ScheduledTask -TaskName $Task; "started"
  }
  'loop' {
    $rng = New-Object Random; $n = (Get-ChildItem $Dirs[0] -Filter 'f-*' -ErrorAction SilentlyContinue).Count
    while ($n -lt 400) {
      $n++
      foreach ($d in $Dirs) {
        $buf = New-Object byte[] ((1 + $rng.Next(4)) * 1MB); $rng.NextBytes($buf)
        $fs = [IO.File]::Create((Join-Path $d "f-$n")); $fs.Write($buf, 0, $buf.Length); $fs.Flush($true); $fs.Close()
        if ($n % 4 -eq 0) {
          $t = Join-Path $d ("f-" + (1 + $rng.Next($n)))
          if (Test-Path $t) { $b2 = New-Object byte[] 131072; $rng.NextBytes($b2); $fs = [IO.File]::Open($t, 'Open'); $fs.Write($b2, 0, $b2.Length); $fs.Flush($true); $fs.Close() }
        }
      }
      Start-Sleep -Seconds 1
    }
  }
  'stop' {
    Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object { $_.CommandLine -match 'guest-load\.ps1' -and $_.CommandLine -match '-Mode loop' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Unregister-ScheduledTask -TaskName $Task -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    foreach ($d in $Dirs) {
      $lines = Get-ChildItem $d -Filter 'f-*' | Sort-Object Name | ForEach-Object { "{0} {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name }
      [IO.File]::WriteAllLines((Manifest $d), [string[]]$lines)
      Write-VolumeCache -DriveLetter $d.Substring(0,1)
      "{0} {1} {2}" -f $d, (Get-FileHash (Manifest $d) -Algorithm SHA256).Hash.ToLower(), $lines.Count
    }
  }
  'check' {
    foreach ($d in $Dirs) {
      $bad = 0; $lines = Get-Content (Manifest $d)
      foreach ($l in $lines) { $h, $f = $l -split ' ', 2; if ((Get-FileHash (Join-Path $d $f) -Algorithm SHA256).Hash.ToLower() -ne $h) { $bad++ } }
      "{0} {1} {2} files, {3} bad" -f $d, ($(if ($bad) {'FAILED'} else {'OK'})), $lines.Count, $bad
    }
  }
}
