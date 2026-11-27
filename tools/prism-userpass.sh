#!/bin/bash
# D11/T10: save a Prism Central username and password for the ev2ahv agents, then test the login.
#   bash ~/ev2ahv/tools/prism-userpass.sh [pc-address]     (default pc-address: 10.12.135.196)
# The password is read without echo and stored in ~/.config/ev2ahv/prism-userpass.env (mode 600, never in git).
# Agents use it with:  set -a; . ~/.config/ev2ahv/prism-userpass.env; set +a   (NTNX_API_KEY must be unset)
set -euo pipefail
PC="${1:-10.12.135.196}"
OUT="$HOME/.config/ev2ahv/prism-userpass.env"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY=python3

read -rp "Prism username for $PC: " U
read -rsp "Password: " P; echo
if [ -z "$U" ] || [ -z "$P" ]; then echo "username and password are required" >&2; exit 1; fi

mkdir -p "$(dirname "$OUT")"
umask 077
printf 'NTNX_PC=%q\nNTNX_USERNAME=%q\nNTNX_PASSWORD=%q\n' "$PC" "$U" "$P" > "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
echo "saved to $OUT (mode $(stat -c %a "$OUT"))"

echo "testing the login (API key ignored for this test)..."
cd "$REPO"
env -u NTNX_API_KEY NTNX_PC="$PC" NTNX_USERNAME="$U" NTNX_PASSWORD="$P" "$PY" -m ev2ahv creds test
