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
    # A symlink inside the tree must not pull a file from outside the repo (a key, /etc/shadow, ...) into the bundle.
    top = os.path.realpath(root) + os.sep
    for _, p in out:
        if not os.path.realpath(p).startswith(top):
            raise SystemExit(f"refusing to bundle {p}: it resolves outside {root}")
    out.append(("ev2ahv-upgrade", os.path.join(root, "appliance", "rootfs", "usr", "local", "sbin", "ev2ahv-upgrade")))
    units = os.path.join(root, "appliance", "rootfs", "usr", "lib", "systemd", "system")
    for n in ("ev2ahv-upgrade.path", "ev2ahv-upgrade.service"):   # UI upgrades (D7), installed by apply
        if os.path.exists(os.path.join(units, n)):
            out.append(("systemd/" + n, os.path.join(units, n)))
    return out


def sign(data, key):
    """Ed25519 signature over data with an openssl private key file (D7). The appliance verifies with the matching
    public key in /etc/ev2ahv/upgrade-keys/."""
    import tempfile
    with tempfile.NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        return subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", key, "-rawin", "-in", f.name],
                              check=True, capture_output=True).stdout


def build(out_dir, min_from="0.2.0", root=ROOT, sign_key=None):
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
        if sign_key:
            sig = sign(data, sign_key)
            ti = tarfile.TarInfo("manifest.json.sig")
            ti.size, ti.mtime = len(sig), int(time.time())
            tf.addfile(ti, io.BytesIO(sig))
    with open(bundle, "rb") as fh:
        digest = sha256_bytes(fh.read())
    with open(bundle + ".sha256", "w") as fh:
        fh.write(f"{digest}  {os.path.basename(bundle)}\n")
    return bundle, manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-from", default="0.2.0", help="oldest installed version this bundle can upgrade")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--sign", metavar="KEY", help="Ed25519 private key (PEM) to sign the manifest with")
    a = ap.parse_args()
    path, man = build(a.out, a.min_from, sign_key=a.sign)
    print(f"{path}: version {man['version']}, {len(man['files'])} files" + (", signed" if a.sign else ""))
