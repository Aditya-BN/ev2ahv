#!/bin/bash
# Static checks for every shell script and PowerShell script (what the CI "lint" job runs).
# ShellCheck runs as a native binary, else the koalaman/shellcheck container. PSScriptAnalyzer: only when pwsh is available
# (CI has it; on the bastion run it with: docker run --rm -v "$PWD:/w:ro" mcr.microsoft.com/powershell pwsh ...).
set -euo pipefail
cd "$(dirname "$0")/.."
mapfile -t SH < <(git ls-files | while read -r f; do
  case "$f" in *.sh) echo "$f" ;; *) [ -f "$f" ] && head -c 64 "$f" | head -1 | grep -qE '^#!.*(ba)?sh( |$)' && echo "$f" ;; esac
done)
echo "shellcheck: ${#SH[@]} scripts"
if command -v shellcheck >/dev/null; then shellcheck -S warning -x "${SH[@]}"
elif command -v docker >/dev/null; then docker run --rm -v "$PWD:/mnt" -w /mnt koalaman/shellcheck:stable -S warning -x "${SH[@]}"
else echo "shellcheck not found (install it to ~/.local/bin or use docker)" >&2; exit 1; fi
if command -v pwsh >/dev/null && pwsh -NoProfile -Command 'exit [int](-not (Get-Module -ListAvailable PSScriptAnalyzer))'; then
  pwsh -NoProfile -Command '$r = Get-ChildItem -Recurse -Filter *.ps1 -Exclude node_modules | ForEach-Object { Invoke-ScriptAnalyzer -Path $_.FullName -Settings tools/PSScriptAnalyzerSettings.psd1 }; $r | Format-Table -AutoSize; if ($r) { exit 1 }'
else echo "PSScriptAnalyzer: skipped (pwsh or the module is missing)"; fi
