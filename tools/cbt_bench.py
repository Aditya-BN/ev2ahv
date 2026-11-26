"""D5 measurement: full resync vs dirty-bitmap resync on a fake source.

Runs where qemu-storage-daemon, qemu-img and qemu-io exist (the appliance builder image):
  docker run --rm -v $PWD:/src -w /src ev2ahv-builder python3 tools/cbt_bench.py [size_gib] [dirty_pct]
The fake guest disk is a fully written raw file served by qemu-storage-daemon; "guest writes" go through its NBD
export, so the dirty bitmap sees them. The engine reads the file directly with O_DIRECT, as on an everRun node.
"""
import json, os, random, shutil, subprocess, sys, tempfile, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import engine  # noqa: E402
from ev2ahv.cbt import QemuBitmap, UnixQmp, ranges_to_chunks  # noqa: E402
from ev2ahv.readers import LocalReader  # noqa: E402
from ev2ahv.state import State  # noqa: E402
from ev2ahv.transport import BlockEndpoint, LocalTransport  # noqa: E402

MiB = 1 << 20
CHUNK = 4 * MiB


def same(a, b):
    return subprocess.run(["cmp", "-s", a, b]).returncode == 0


def guest_writes(sock, size, pct, rng):
    """Write 64 KiB of new data into pct % of the 4 MiB chunks, through the NBD export."""
    n = max(1, int(size // CHUNK * pct / 100))
    idxs = rng.sample(range(size // CHUNK), n)
    cmds = []
    for i in idxs:
        off = i * CHUNK + rng.randrange(0, CHUNK - 65536, 4096)
        cmds += ["-c", f"write -P {rng.randrange(1, 255)} {off} 65536"]
    subprocess.run(["qemu-io", *cmds, "--image-opts", f"driver=nbd,server.type=unix,server.path={sock},export=guest"],
                   check=True, capture_output=True)
    return sorted(idxs)


def main():
    size = int(float(sys.argv[1] if len(sys.argv) > 1 else 2) * 1024) * MiB
    pct = float(sys.argv[2] if len(sys.argv) > 2 else 2)
    rng = random.Random(5)
    w = tempfile.mkdtemp(prefix="cbt-", dir="/var/tmp")
    src, dst, qmp_sock, nbd_sock = (os.path.join(w, n) for n in ("src.raw", "dst.raw", "qmp.sock", "nbd.sock"))
    subprocess.run(["dd", "if=/dev/urandom", f"of={src}", "bs=4M", f"count={size // CHUNK}", "status=none"], check=True)
    with open(dst, "wb") as f:
        f.truncate(size)
    qsd = subprocess.Popen(["qemu-storage-daemon",
                            "--blockdev", f"driver=file,node-name=file0,filename={src},cache.direct=on",
                            "--blockdev", "driver=raw,node-name=disk0,file=file0",
                            "--nbd-server", f"addr.type=unix,addr.path={nbd_sock}",
                            "--export", "type=nbd,id=guest,node-name=disk0,name=guest,writable=on",
                            "--chardev", f"socket,id=qmp,path={qmp_sock},server=on,wait=off", "--monitor", "chardev=qmp"])
    out = {"size_gib": size / 1024 ** 3, "dirty_pct": pct, "chunk_mib": CHUNK // MiB}
    try:
        for _ in range(50):
            if os.path.exists(qmp_sock) and os.path.exists(nbd_sock):
                break
            time.sleep(0.1)
        cbt = QemuBitmap(UnixQmp(qmp_sock), "disk0", nbd_sock)
        state = State(os.path.join(w, "state.db"))
        reader = LocalReader(src, chunk=CHUNK)
        writer = BlockEndpoint(LocalTransport(), dst, chunk=CHUNK, writable=True)
        algo = engine.negotiate(reader, writer)
        cbt.start()                                   # tracking starts before the seed pass
        t = time.monotonic()
        engine.sync(reader, writer, state, "d", algo)
        out["seed_s"] = round(time.monotonic() - t, 2)
        cbt.drop(cbt.rotate())                        # the seed covered everything up to here

        # round 1: guest writes, then the current resync (full read of every allocated chunk)
        w1 = guest_writes(nbd_sock, size, pct, rng)
        t = time.monotonic()
        full = engine.sync(reader, writer, state, "d", algo)
        out["full_resync"] = {"seconds": round(time.monotonic() - t, 2), "read_chunks": full["scanned"],
                              "sent_chunks": full["sent"], "changed_chunks": len(w1)}
        out["full_resync"]["target_equals_source"] = same(src, dst)
        cbt.drop(cbt.rotate())                        # synced by the full pass

        # round 2: the same amount of guest writes, then the dirty-bitmap resync
        w2 = guest_writes(nbd_sock, size, pct, rng)
        t = time.monotonic()
        frozen = cbt.rotate()
        ranges = cbt.dirty(frozen)
        chunks = ranges_to_chunks(ranges, CHUNK)
        t_map = time.monotonic() - t
        dirty = engine.sync_dirty(reader, writer, state, "d", algo, chunks)
        for _ in range(20):                           # the export goes away once qemu-img has disconnected
            try:
                cbt.drop(frozen)
                break
            except RuntimeError:
                time.sleep(0.1)
        out["cbt_resync"] = {"seconds": round(time.monotonic() - t, 2), "bitmap_query_s": round(t_map, 3),
                             "read_chunks": dirty["read_chunks"], "sent_chunks": dirty["sent"],
                             "changed_chunks": len(w2), "bitmap_matches_writes": chunks == w2}
        writer.close()
        out["cbt_resync"]["target_equals_source"] = same(src, dst)
        out["speedup"] = round(out["full_resync"]["seconds"] / max(out["cbt_resync"]["seconds"], 1e-3), 1)
        reader.close()
        state.close()
    finally:
        qsd.terminate()
        qsd.wait()
        shutil.rmtree(w, ignore_errors=True)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
