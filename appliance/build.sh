#!/bin/bash
# Build dist/ev2ahv-<VERSION>.qcow2. Needs docker (privileged containers). No sudo, no KVM.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=$PWD; VER=$(cat VERSION)
[ -n "$REPO" ] && [ "$REPO" != / ] && [ -f "$REPO/appliance/build.sh" ] || { echo "refusing: not in the repo root" >&2; exit 2; }
[[ "$VER" =~ ^[0-9A-Za-z._-]+$ ]] || { echo "refusing: odd VERSION '$VER'" >&2; exit 2; }
mkdir -p build/wheels dist
if [ ! -f web/dist/index.html ] || [ -n "$(find web/src web/index.html web/package.json -newer web/dist/index.html 2>/dev/null | head -1)" ]; then
  (cd web && npm ci && npm run build)
fi
docker build -q -t ev2ahv-builder appliance/builder >/dev/null
if [ ! -f build/wheels/.ok ] || [ appliance/requirements.txt -nt build/wheels/.ok ]; then
  rm -f -- "${REPO:?}/build/wheels/"*   # only the wheel cache this script created
  docker run --rm -v "$REPO/appliance:/a:ro" -v "$REPO/build/wheels:/w" ev2ahv-builder bash -c \
    'python3.12 -m pip install -q -U pip && python3.12 -m pip download -q --only-binary=:all: -r /a/requirements.txt -d /w'
  touch build/wheels/.ok
fi
docker run --rm --privileged -v "$REPO:/src:ro" -v "$REPO/build:/out" ev2ahv-builder /src/appliance/scripts/mkimage.sh
cp build/ev2ahv.qcow2 "dist/ev2ahv-$VER.qcow2"
(cd dist && sha256sum "ev2ahv-$VER.qcow2" > "ev2ahv-$VER.qcow2.sha256")
python3 - "$VER" <<'P'
import json,subprocess,sys,datetime,os
v=sys.argv[1]
rpms=[dict(zip(("name","version"),l.split("\t"))) for l in open("build/rpm.list").read().splitlines()]
pips=[l for l in open("build/pip.list").read().splitlines() if l]
vs=json.loads(subprocess.check_output(["qemu-img","info","--output=json","dist/ev2ahv-%s.qcow2"%v]))["virtual-size"] if False else None
json.dump({"version":v,"git_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
 "build_date_utc":datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
 "image":"ev2ahv-%s.qcow2"%v,"size_bytes":os.path.getsize("dist/ev2ahv-%s.qcow2"%v),"virtual_size":"20GiB",
 "rpm_count":len(rpms),"rpms":rpms,"pip":pips},open("dist/MANIFEST.json","w"),indent=1)
P
docker run --rm -v "$REPO/build/tree:/t:ro" anchore/syft:latest dir:/t -q -o spdx-json > "dist/ev2ahv-$VER.sbom.spdx.json" || echo "syft failed"
ls -l dist
