"""Build an appliance upgrade bundle (D7): dist/ev2ahv-upgrade-<version>.tar.gz plus .sha256.

  python3 tools/make_bundle.py [--min-from 0.2.0]

Needs web/dist (npm run build) and build/wheels (appliance/build.sh downloads them). The bundle holds:
  app/                 ev2ahv, server, prep, web/dist, VERSION, requirements.txt (what /opt/ev2ahv/app holds)
  wheels/              the pinned wheels, so the appliance can rebuild its venv offline
  ev2ahv-upgrade       the upgrade tool itself (installed to /usr/local/sbin)
  manifest.json        version, min_from, git commit, requirements sha256, sha256 of every file
Apply on the appliance: sudo ev2ahv-upgrade apply ev2ahv-upgrade-<version>.tar.gz
"""
import argparse, hashlib, io, json, os, subprocess, tarfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP = ("__pycache__", ".pyc", "run-dev.sh")


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def collect(root=ROOT):
    """(archive name, source path) for every file in the bundle."""
    out = []

    def walk(src, dst):
        for d, dirs, files in os.walk(src):
            dirs[:] = sorted(x for x in dirs if x not in ("__pycache__", "tests", "node_modules"))
            for f in sorted(files):
                if not any(s in f for s in SKIP):
                    p = os.path.join(d, f)
                    out.append((os.path.join(dst, os.path.relpath(p, src)), p))
    for name in ("ev2ahv", "server", "prep"):
        walk(os.path.join(root, name), "app/" + name)
    walk(os.path.join(root, "web", "dist"), "app/web/dist")
    out.append(("app/VERSION", os.path.join(root, "VERSION")))
    out.append(("app/requirements.txt", os.path.join(root, "appliance", "requirements.txt")))
    walk(os.path.join(root, "build", "wheels"), "wheels")
    out = [(a, p) for a, p in out if not a.endswith("/.ok")]
    out.append(("ev2ahv-upgrade", os.path.join(root, "appliance", "rootfs", "usr", "local", "sbin", "ev2ahv-upgrade")))
    return out


def build(out_dir, min_from="0.2.0", root=ROOT):
    with open(os.path.join(root, "VERSION")) as fh:
        version = fh.read().strip()
    with open(os.path.join(root, "appliance", "requirements.txt"), "rb") as fh:
        req_sha = sha256_bytes(fh.read())
    if not os.path.exists(os.path.join(root, "web", "dist", "index.html")):
        raise SystemExit("web/dist is missing: run npm run build in web/ first")
    if not any(f.endswith(".whl") for f in os.listdir(os.path.join(root, "build", "wheels"))):
        raise SystemExit("build/wheels is empty: run appliance/build.sh first")
    files = collect(root)
    try:
        commit = subprocess.check_output(["git", "-C", root, "rev-parse", "HEAD"], text=True,
                                         stderr=subprocess.DEVNULL).strip()
    except Exception:
        commit = ""
    manifest = {"version": version, "min_from": min_from, "git_commit": commit,
                "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "requirements_sha256": req_sha,
                "files": {}}
    for arc, path in files:
        with open(path, "rb") as fh:
            manifest["files"][arc] = sha256_bytes(fh.read())
    os.makedirs(out_dir, exist_ok=True)
    bundle = os.path.join(out_dir, f"ev2ahv-upgrade-{version}.tar.gz")
    with tarfile.open(bundle, "w:gz") as tf:
        for arc, path in files:
            ti = tf.gettarinfo(path, arc)
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            with open(path, "rb") as fh:
                tf.addfile(ti, fh)
        data = json.dumps(manifest, indent=1).encode()
        ti = tarfile.TarInfo("manifest.json")
        ti.size, ti.mtime = len(data), int(time.time())
        tf.addfile(ti, io.BytesIO(data))
    with open(bundle, "rb") as fh:
        digest = sha256_bytes(fh.read())
    with open(bundle + ".sha256", "w") as fh:
        fh.write(f"{digest}  {os.path.basename(bundle)}\n")
    return bundle, manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-from", default="0.2.0", help="oldest installed version this bundle can upgrade")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    a = ap.parse_args()
    path, man = build(a.out, a.min_from)
    print(f"{path}: version {man['version']}, {len(man['files'])} files")
