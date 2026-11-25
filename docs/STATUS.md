# ev2ahv status and resume notes (2026-11-25 WIB)

## Done and verified
- Stage A: engine, CLI, sync, readers, AHV writer (helper VM `ev2ahv-writer` 10.12.136.211), image fallback, docs.
- Stage B: Rocky 9 FT and Windows Server 2022 HA migrated from everRun, verified, rolled back. Results in docs/results.md.
- Addendum: Move-like guest prep (auto WinRM/ssh, manual bundle), graded prep-check gating cutover, power-order
  enforcement, PCI to SCSI conversion (default, `target.convert_to_scsi: false` to opt out), multi-disk ext4 /
  spanned LVM. All tested on both lab guests.
- Stage C1: Move 6.2.0 reference in docs/move-reference/ (74 screens, redacted HAR, api-notes, UX-SPEC, feature
  matrix). Wizard steps 4-6 are reconstructed, not observed: with owner approval a throwaway plan was attempted, but Move itself failed to create it (HTTP 500, code 20543: the appliance cannot resolve its vCenter hostname). Steps 1-3 are observed.

## In progress: Stage C3/C4 step 1 (API contract + clickable mock UI)
- server/ev2ahv_server/models.py (contract) and store.py (SQLite docs, write-only secrets, events, jobs) written.
- Still to write: server api.py (FastAPI app, /api/v2 Move-shaped routes, cookie login reusing ~/.config/ev2ahv/ui.json),
  mock seeding + simulator, HTTPS (self-signed) on 8443, systemd unit.
- Then: frontend in ~/ev2ahv/web (React + Vite + TS), planned as a Sonnet agent working from UX-SPEC.md,
  the screenshots and the generated openapi.json, on mock data first. Then side-by-side screenshots vs Move.
- fastapi 0.128 / uvicorn 0.39 / pydantic 2 installed in ~/ev2ahv/.venv. npm 10.9 + registry reachable.

## Lab state
- everRun: ev2ahv-src-rocky9 (FT, 4 disks) and ev2ahv-src-win22 (HA) running on everRun.
- AHV: ev2ahv-writer ON; ev2ahv-win22-ahv and ev2ahv-rocky9-multi OFF (kept); staging disks on the writer.
- Lab bridge: REQ-001 and REQ-002 done. Lab session: session_012MCA4oeMjsZRVhbBhzuie4 (shares ~/migration-tool;
  do not use `claude -c`). Reach it with the send_message tool.
- Credentials: Prism key ~/nutanix/.env (PC_API_KEY); Windows guest pw ~/.config/ev2ahv/win.env; everRun admin
  via ~/everrun-lab/av.sh; ev2ahv ssh key ~/.config/ev2ahv/id_ed25519 (on both nodes' root).

## Pending, owner decisions
- Final cleanup (B5): delete all ev2ahv-* on AHV, ask the lab to remove the ev2ahv key from the nodes. Owner: "later".
- Not tested: ghost NIC removal on a real ghost, NGT install, Windows manual cleanup on AHV, Prism user/password login.
- Known limit: downtime scales with disk size (final sync re-reads both sides).
