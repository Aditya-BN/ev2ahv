"""Run one engine step for one VM in its own process and report progress as JSON lines on stdout.

python -m ev2ahv_server.runner <plan.yaml> <action> [--force] [--assume-stopped]
Lines: EV2AHV {"progress": {...}} while syncing, then EV2AHV {"result": ...} or EV2AHV {"error": "..."}.
A separate process keeps pause and cancel simple (terminate it; sync resumes from its last committed chunk)
and keeps a crash in one migration away from the server. Prism credentials come from the environment.
"""
import json, logging, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from ev2ahv import credentials, plan as planlib  # noqa: E402
from ev2ahv.prism import Prism  # noqa: E402
from ev2ahv.workflow import Migration  # noqa: E402

ACTIONS = ("prep-check", "guest-prep", "seed", "resync", "cutover", "rollback", "discard-target", "guest-cleanup",
           "cleanup", "status", "test", "discard-test", "cleanup-staging")


def emit(**kw):
    sys.stdout.write("EV2AHV " + json.dumps(kw, default=str) + "\n")
    sys.stdout.flush()


def next_stage(step, image_mode=False, converts=False):
    """The cutover stage that starts after engine step `step` is marked done (D20)."""
    return {"stopped": "final sync", "synced": "image upload" if image_mode else "preparing disks",
            "quiesced": "creating AHV VM", "created": "powering on", "powered": "verifying",
            "verified": "converting to SCSI" if converts else "finishing"}.get(step, "finishing")


def watch_stages(m):
    """Emit EV2AHV {"stage": ...} as the cutover moves on, so the UI can say what it is doing."""
    mark = m.mark

    def marked(step):
        mark(step)
        emit(stage=next_stage(step, m.image_mode, m.wants_conversion()))
    m.mark = marked
    emit(stage="stopping source")


def main():
    path, action = sys.argv[1], sys.argv[2]
    if action not in ACTIONS:
        sys.exit(f"unknown action {action}")
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.Formatter.converter = time.gmtime
    p = planlib.load(path)
    sizes = {d["name"]: 0 for d in p["disks"]}
    done = dict(sizes)
    sent = dict(sizes)
    last = [0.0]

    def progress(key, idx, total, stats):
        sizes[key], done[key], sent[key] = total, idx, stats.get("sent", 0)
        now = time.time()
        if now - last[0] >= 1 or idx >= total:
            last[0] = now
            emit(progress={"disk": key, "done": sum(done.values()), "total": sum(sizes.values()) or 1,
                           "sent_chunks": sum(sent.values()), "chunk_mib": int(p["sync"]["chunk_mib"])})

    m = Migration(p, Prism(credentials.load()), progress=progress)
    if action == "cutover":
        watch_stages(m)
    try:
        if action == "prep-check":
            r = m.prep_check()
        elif action == "cutover":
            r = m.cutover(assume_stopped="--assume-stopped" in sys.argv, force="--force" in sys.argv)
        elif action == "cleanup":
            r = m.cleanup(delete_target=True)
        elif action == "cleanup-staging":   # D24: staging disks only, the migrated VM is kept
            r = m.cleanup(delete_target=False)
        else:
            r = getattr(m, action.replace("-", "_"))()
        emit(result=r)
    except Exception as e:  # reported to the UI; the traceback goes to the job log
        logging.exception("%s failed", action)
        emit(error=str(e))
        sys.exit(1)
    finally:
        m.close()


if __name__ == "__main__":
    main()
