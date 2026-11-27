"""Chunk sync: send only chunks whose content differs, committing progress after each fsync."""
import logging, random, time

from .helper.ev2ahv_helper import digest, is_zero

log = logging.getLogger("ev2ahv.sync")
ZERO = "z"
PREFERENCE = ("xxh64", "blake2b", "sha256")


def negotiate(source, target, wanted="auto"):
    common = set(source.caps()["algos"]) & set(target.caps()["algos"])
    if wanted != "auto":
        if wanted not in common:
            raise RuntimeError(f"hash {wanted} is not available on both sides (common: {sorted(common)})")
        return wanted
    return next(a for a in PREFERENCE if a in common)


def fingerprint(data, algo):
    return ZERO if is_zero(data) else digest(data, algo)


class Stats(dict):
    def __init__(self):
        super().__init__(scanned=0, sent=0, sent_bytes=0, zero_written=0, skipped=0, repaired=0, seconds=0.0)


def sync(source, target, state, key, algo, verify_target=False, batch=32, progress=None, stop=None):
    """One pass. verify_target=True hashes the real target instead of trusting recorded digests.
    A new AHV vdisk reads as zeros, so a chunk with no record is treated as zero."""
    size = source.size()
    if target.size() < size:
        raise RuntimeError(f"target ({target.size()}) is smaller than source ({size})")
    chunk = source.chunk
    state.ensure_disk(key, size, chunk, algo)
    start = state.begin_pass(key)
    known = state.digests(key, start)
    stats, began, pending = Stats(), time.monotonic(), []
    total = (size + chunk - 1) // chunk
    target_iter = target.hashes(algo, start, limit=size) if verify_target else None
    observed = []   # verified target digests that differ from the record

    def flush(next_pos):
        updates = list(observed)
        observed.clear()
        if pending:
            wanted = dict(pending)
            items = []
            for idx, data in source.read([i for i, _ in pending]):
                dg = fingerprint(data, algo)   # the bytes actually read, so a racing write shows up next pass
                if dg == wanted[idx]:
                    updates.append((idx, dg))
                    continue
                items.append((idx, data))
                updates.append((idx, dg))
                stats["sent"] += 1
                stats["sent_bytes"] += len(data)
                stats["zero_written"] += dg == ZERO
            if items:
                target.write_batch(items)
            pending.clear()
        state.commit(key, updates, next_pos)

    for idx, src_dg in source.hashes(algo, start):
        if stop and stop():
            flush(idx)
            raise InterruptedError(f"stopped at chunk {idx}")
        if verify_target:
            t_idx, have = next(target_iter)
            if t_idx != idx:
                raise RuntimeError(f"target hash out of step: {t_idx} vs {idx}")
            if have != known.get(idx, ZERO):
                stats["repaired"] += have != src_dg
                observed.append((idx, have))
        else:
            have = known.get(idx, ZERO)
        stats["scanned"] += 1
        if src_dg == have:
            stats["skipped"] += 1
        else:
            pending.append((idx, have))
        if len(pending) >= batch or (idx + 1) % 256 == 0:
            flush(idx + 1)
            if progress:
                progress(key, idx + 1, total, stats)
    flush(total)
    if target_iter is not None:
        target_iter.close()
    state.end_pass(key)
    stats["seconds"] = round(time.monotonic() - began, 3)
    if progress:
        progress(key, total, total, stats)
    state.event("sync", disk=key, verify=verify_target, **stats)
    log.info("%s: %s", key, dict(stats))
    return stats


def sync_dirty(source, target, state, key, algo, dirty, batch=32, progress=None, stop=None):
    """Changed-block pass (D5): read only the chunks a tracker reported dirty since the last pass, and send the ones
    whose content differs from the record. Valid only if the tracker was recording without a gap since the last full
    pass started; the caller falls back to sync() otherwise. Never used while a full pass is half done."""
    d = state.disk(key)
    if d is None or d["pass_active"] or not d["passes"]:
        raise RuntimeError(f"{key}: no completed full pass to build on; run a full pass")
    size, chunk = source.size(), source.chunk
    total = (size + chunk - 1) // chunk
    idxs = sorted(i for i in set(dirty) if 0 <= i < total)
    known = state.digests(key)
    stats, began = Stats(), time.monotonic()
    stats["read_chunks"] = 0
    for b in range(0, len(idxs), batch):
        if stop and stop():
            raise InterruptedError(f"stopped after {b} of {len(idxs)} dirty chunks")
        items, updates = [], []
        for idx, data in source.read(idxs[b:b + batch]):
            dg = fingerprint(data, algo)
            stats["read_chunks"] += 1
            stats["scanned"] += 1
            updates.append((idx, dg))
            if dg == known.get(idx, ZERO):
                stats["skipped"] += 1
                continue
            items.append((idx, data))
            stats["sent"] += 1
            stats["sent_bytes"] += len(data)
            stats["zero_written"] += dg == ZERO
        if items:
            target.write_batch(items)
        state.commit(key, updates, 0)
        if progress:
            progress(key, min(b + batch, len(idxs)), max(len(idxs), 1), stats)
    stats["seconds"] = round(time.monotonic() - began, 3)
    state.event("sync-dirty", disk=key, dirty=len(idxs), **stats)
    log.info("%s (dirty %d of %d chunks): %s", key, len(idxs), total, dict(stats))
    return stats


def final_sync(source, target, state, key, algo, rounds=5, **kw):
    """A fast source-only pass sends what changed, then verified passes run until both sides match."""
    sync(source, target, state, key, algo, verify_target=False, **kw)
    for n in range(1, rounds + 1):
        stats = sync(source, target, state, key, algo, verify_target=True, **kw)
        if stats["sent"] == 0:
            return n, stats
    raise RuntimeError(f"{key}: still differing after {rounds} verified passes. Is the source really stopped?")


FINAL_VERIFY = ("full", "sample", "none")
SAMPLE_MIN, SAMPLE_FRACTION = 256, 0.02


def sample_check(source, target, key, algo, state, total, dirty, rng=None):
    """Read a random sample of chunks (every chunk the dirty pass wrote, plus 2 percent of the disk, at least 256)
    from both sides and compare them. Returns the number of chunks that differ."""
    rng = rng or random.Random()
    n = min(total, max(SAMPLE_MIN, int(total * SAMPLE_FRACTION)))
    picks = sorted(set(rng.sample(range(total), n)) | {i for i in dirty if 0 <= i < total})
    bad, checked = 0, 0
    for b in range(0, len(picks), 32):
        part = picks[b:b + 32]
        for (i, a), (j, c) in zip(source.read(part), target.read(part)):
            checked += 1
            bad += fingerprint(a, algo) != fingerprint(c, algo)
    state.event("final-sample", disk=key, checked=checked, mismatched=bad)
    log.info("%s: final sample check, %d chunks, %d differ", key, checked, bad)
    return bad


def final_sync_dirty(source, target, state, key, algo, dirty, verify="full", rounds=5, **kw):
    """Final sync from a dirty-chunk list captured with the guest stopped (D5, docs/cbt.md): a dirty pass replaces the
    full source-only pass, then `verify` decides how the result is checked:
      full    the verified passes of final_sync (hash the real target too, repair, repeat until clean). Default.
      sample  compare a random sample plus every chunk the dirty pass wrote; any difference means the full passes.
      none    trust the dirty pass.
    Returns (verified rounds, stats); stats carry final_mode and the dirty chunk count."""
    if verify not in FINAL_VERIFY:
        raise ValueError(f"final_verify must be one of {FINAL_VERIFY}")
    kw = {k: v for k, v in kw.items() if k in ("batch", "progress", "stop")}
    stats = sync_dirty(source, target, state, key, algo, dirty, **kw)
    stats["final_mode"], stats["dirty_chunks"] = "dirty+" + verify, len(set(dirty))
    if verify == "none":
        return 0, stats
    if verify == "sample":
        total = (source.size() + source.chunk - 1) // source.chunk
        if sample_check(source, target, key, algo, state, total, dirty) == 0:
            return 0, stats
        log.warning("%s: sample check found differences, running the full verified passes", key)
        stats["final_mode"] = "dirty+sample->full"
    for n in range(1, rounds + 1):
        vs = sync(source, target, state, key, algo, verify_target=True, **kw)
        if vs["sent"] == 0:
            return n, dict(vs, final_mode=stats["final_mode"], dirty_chunks=stats["dirty_chunks"])
    raise RuntimeError(f"{key}: still differing after {rounds} verified passes. Is the source really stopped?")
