#!/usr/bin/env python3
"""Run PowerShell on a Windows guest over WinRM (NTLM, message encryption).

Usage: winrm_run.py <host> <script.ps1|-> [args...]   # '-' reads the script from stdin
       winrm_run.py <host> --put <local> <remote>     # copy a file in 1.5 KB chunks (the WinRM command line is limited to 8 KB)
Password from EV2AHV_WIN_PASS, user from EV2AHV_WIN_USER (default Administrator).
"""
import base64, os, sys
import winrm

host = sys.argv[1]
s = winrm.Session(f"http://{host}:5985/wsman", auth=(os.environ.get("EV2AHV_WIN_USER", "Administrator"),
                  os.environ["EV2AHV_WIN_PASS"]), transport="ntlm", server_cert_validation="ignore",
                  read_timeout_sec=900, operation_timeout_sec=850)


def ps(script):
    r = s.run_ps(script)
    sys.stdout.write(r.std_out.decode(errors="replace"))
    err = r.std_err.decode(errors="replace")
    if r.status_code and err:
        sys.stderr.write(err[-3000:])
    return r.status_code


if sys.argv[2] == "--put":
    data, remote = open(sys.argv[3], "rb").read(), sys.argv[4].replace("'", "''")
    rc = ps(f"New-Item -ItemType Directory -Force -Path (Split-Path '{remote}') | Out-Null; Set-Content -Path '{remote}' -Value $null -Encoding Byte -ErrorAction SilentlyContinue; Remove-Item '{remote}' -ErrorAction SilentlyContinue")
    for i in range(0, len(data), 1500):
        b64 = base64.b64encode(data[i:i + 1500]).decode()
        rc = ps(f"$b=[Convert]::FromBase64String('{b64}'); $f=[IO.File]::Open('{remote}','Append'); $f.Write($b,0,$b.Length); $f.Close()")
        if rc:
            sys.exit(rc)
    sys.exit(ps(f"(Get-Item '{remote}').Length"))
script = sys.stdin.read() if sys.argv[2] == "-" else open(sys.argv[2]).read()
args = " ".join("'" + a.replace("'", "''") + "'" for a in sys.argv[3:])
if args:
    script = f"& {{ {script} }} {args}"
sys.exit(ps(script))
