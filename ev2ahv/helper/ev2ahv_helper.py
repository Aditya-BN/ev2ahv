#!/usr/bin/env python3
"""ev2ahv block helper. Single file, python3.6+ stdlib only (xxhash used if already present).

Runs on the source or writer side over ssh. `hash` and `read` open the device O_RDONLY.
Only `write` opens for writing, and it refuses a device that is mounted or held.

Frames on the wire: >QI (chunk index, payload length) + payload. Index END closes the stream,
index FLUSH asks the writer to fsync and acknowledge. Without FLUSH nothing is acknowledged, so a client may send
several batches and ask for one fsync at the end. A length with the COMPRESSED bit set carries a zlib stream of the
chunk (only when the peer asked for `--compress`; a frame that does not shrink is sent plain).
"""
import argparse
import errno
import hashlib
import json
import mmap
import os
import stat
import struct
import sys
import time
import zlib

VERSION = "1"
FRAME = struct.Struct(">QI")
END = 2 ** 64 - 1
FLUSH = 2 ** 64 - 2
COMPRESSED = 1 << 31
ZLIB_LEVEL = 1

try:
    import xxhash
except ImportError:
    xxhash = None


def algorithms():
    algos = ["blake2b", "sha256"]
    return ["xxh64"] + algos if xxhash else algos


def digest(data, algo):
    if algo == "xxh64":
        if not xxhash:
            raise ValueError("xxhash is not available")
        return xxhash.xxh64(data).hexdigest()
    if algo == "blake2b":
        return hashlib.blake2b(data, digest_size=16).hexdigest()
    if algo == "sha256":
        return hashlib.sha256(data).hexdigest()
    raise ValueError("unknown algorithm: %s" % algo)


def is_zero(data):
    return not any_nonzero(memoryview(data))


def any_nonzero(view):
    step = 65536
    zero = bytes(step)
    for off in range(0, len(view), step):
        part = view[off:off + step]
        if part != zero[:len(part)]:
            return True
    return False


class Throttle:
    def __init__(self, max_mbps):
        self.rate = max_mbps * 1024 * 1024 if max_mbps else 0
        self.start = time.monotonic()
        self.sent = 0

    def account(self, n):
        if not self.rate:
            return
        self.sent += n
        delay = self.sent / self.rate - (time.monotonic() - self.start)
        if delay > 0:
            time.sleep(delay)


def device_size(fd):
    return os.lseek(fd, 0, os.SEEK_END)


class Reader:
    """Read-only chunk reader. Prefers O_DIRECT so a running guest's writes are never hidden by
    a stale page cache; falls back to buffered reads with cache drops."""

    def __init__(self, path, chunk, limit=None):
        self.limit = limit
        self.chunk = chunk
        self.buf = mmap.mmap(-1, chunk)
        self.direct = False
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        try:
            self.fd = os.open(path, flags | os.O_DIRECT)
            self.direct = True
            self.size = device_size(self.fd)
            if self.size:
                os.lseek(self.fd, 0, os.SEEK_SET)
                os.readv(self.fd, [self.buf])
        except OSError:
            if self.direct:
                os.close(self.fd)
            self.direct = False
            self.fd = os.open(path, flags)
            self.size = device_size(self.fd)
        if self.limit is not None:
            self.size = min(self.size, self.limit)
        self.sparse = stat.S_ISREG(os.fstat(self.fd).st_mode)

    def is_hole(self, index):
        """True if the whole chunk is a hole in a sparse file, so it reads as zeros without I/O."""
        if not self.sparse:
            return False
        offset = index * self.chunk
        try:
            return os.lseek(self.fd, offset, os.SEEK_DATA) >= offset + min(self.chunk, self.size - offset)
        except OSError as e:
            if e.errno == errno.ENXIO:
                return True
            return False

    @property
    def count(self):
        return (self.size + self.chunk - 1) // self.chunk

    def read(self, index):
        offset = index * self.chunk
        want = min(self.chunk, self.size - offset)
        if want <= 0:
            raise ValueError("chunk %d is past end of device" % index)
        if not self.direct:
            os.posix_fadvise(self.fd, offset, want, os.POSIX_FADV_DONTNEED)
        got = 0
        os.lseek(self.fd, offset, os.SEEK_SET)
        if self.direct:
            while got < want:
                n = os.readv(self.fd, [self.buf])
                if n <= 0:
                    break
                if got == 0 and n >= want:
                    got = n
                    break
                raise IOError("short direct read at chunk %d" % index)
            data = self.buf[:want]
        else:
            parts = []
            while got < want:
                part = os.read(self.fd, want - got)
                if not part:
                    break
                parts.append(part)
                got += len(part)
            data = b"".join(parts)
            os.posix_fadvise(self.fd, offset, want, os.POSIX_FADV_DONTNEED)
        if got < want:
            raise IOError("short read at chunk %d: %d of %d" % (index, got, want))
        return data


def cmd_caps(_args):
    print(json.dumps({"version": VERSION, "python": sys.version.split()[0], "algos": algorithms()}))


def cmd_size(args):
    fd = os.open(args.device, os.O_RDONLY)
    try:
        print(device_size(fd))
    finally:
        os.close(fd)


def cmd_hash(args):
    reader = Reader(args.device, args.chunk, args.limit)
    throttle = Throttle(args.max_mbps)
    end = reader.count if args.end is None else min(args.end, reader.count)
    out = sys.stdout
    for index in range(args.start, end):
        if reader.is_hole(index):
            out.write("%d z\n" % index)
        else:
            data = reader.read(index)
            out.write("%d %s\n" % (index, "z" if is_zero(data) else digest(data, args.algo)))
            throttle.account(len(data))
        if index % 16 == 0:
            out.flush()
    out.write("done %d\n" % end)
    out.flush()


def pack_payload(data, compress):
    """(length field, payload) for one frame."""
    if compress:
        packed = zlib.compress(data, ZLIB_LEVEL)
        if len(packed) < len(data):
            return len(packed) | COMPRESSED, packed
    return len(data), data


def cmd_read(args):
    reader = Reader(args.device, args.chunk, args.limit)
    throttle = Throttle(args.max_mbps)
    out = sys.stdout.buffer
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        index = int(line)
        data = reader.read(index)
        length, payload = pack_payload(data, args.compress)
        out.write(FRAME.pack(index, length))
        out.write(payload)
        out.flush()
        throttle.account(len(data))
    out.write(FRAME.pack(END, 0))
    out.flush()


def in_use(device):
    real = os.path.realpath(device)
    if not real.startswith("/dev/"):
        return None
    name = os.path.basename(real)
    sys_dir = "/sys/class/block/%s" % name
    if not os.path.isdir(sys_dir):
        return None
    names = {name}
    for entry in os.listdir(sys_dir):
        if entry.startswith(name):
            names.add(entry)
    with open("/proc/mounts") as fh:
        for line in fh:
            src = os.path.realpath(line.split()[0])
            if src.startswith("/dev/") and os.path.basename(src) in names:
                return "mounted: %s" % src
    for part in names:
        holders = "/sys/class/block/%s/holders" % part
        if os.path.isdir(holders) and os.listdir(holders):
            return "held by %s" % ",".join(os.listdir(holders))
    return None


def read_exact(stream, n):
    parts = []
    while n:
        part = stream.read(n)
        if not part:
            raise EOFError("stream closed mid-frame")
        parts.append(part)
        n -= len(part)
    return b"".join(parts)


def cmd_write(args):
    problem = in_use(args.device)
    if problem:
        sys.exit("refusing to write %s: %s" % (args.device, problem))
    fd = os.open(args.device, os.O_WRONLY | getattr(os, "O_CLOEXEC", 0))
    mode = os.fstat(fd).st_mode
    if not (stat.S_ISBLK(mode) or stat.S_ISREG(mode)):   # never a char device, fifo, directory or socket
        os.close(fd)
        sys.exit("refusing to write %s: not a block device or a regular file" % args.device)
    if args.chunk <= 0:
        os.close(fd)
        sys.exit("refusing to write: --chunk must be positive")
    size = device_size(fd)
    stream = sys.stdin.buffer
    count = 0
    while True:
        index, length = FRAME.unpack(read_exact(stream, FRAME.size))
        if index == END:
            break
        if index == FLUSH:
            os.fsync(fd)
            print("flushed %d" % count, flush=True)
            continue
        offset = index * args.chunk
        packed = bool(length & COMPRESSED)
        if packed:
            if not args.compress:
                sys.exit("compressed frame without --compress")
            length &= ~COMPRESSED
            if length > args.chunk:
                sys.exit("frame out of bounds: chunk %d length %d" % (index, length))
            try:
                data = zlib.decompressobj().decompress(read_exact(stream, length), args.chunk + 1)
            except zlib.error as e:
                sys.exit("bad compressed frame for chunk %d: %s" % (index, e))
            length = len(data)
            if length > args.chunk:
                sys.exit("frame out of bounds: chunk %d inflates past the chunk size" % index)
        else:
            if length > args.chunk:
                sys.exit("frame out of bounds: chunk %d length %d" % (index, length))
            data = None
        if offset + length > size:
            sys.exit("frame out of bounds: chunk %d length %d" % (index, length))
        if data is None:
            data = read_exact(stream, length)
        done = 0
        while done < length:
            done += os.pwrite(fd, data[done:], offset + done)
        count += 1
    os.fsync(fd)
    os.close(fd)
    try:
        print("ok %d" % count, flush=True)
    except BrokenPipeError:
        pass   # the client hangs up after the END frame; data is already fsynced


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ev2ahv_helper")
    sub = parser.add_subparsers(dest="cmd")
    sub.required = True
    sub.add_parser("caps").set_defaults(fn=cmd_caps)
    p = sub.add_parser("size")
    p.add_argument("device")
    p.set_defaults(fn=cmd_size)
    for name, fn in (("hash", cmd_hash), ("read", cmd_read), ("write", cmd_write)):
        p = sub.add_parser(name)
        p.add_argument("device")
        p.add_argument("--chunk", type=int, default=4 * 1024 * 1024)
        p.add_argument("--max-mbps", type=float, default=0)
        p.add_argument("--compress", action="store_true", help="zlib-compress frames (read: replies, write: requests)")
        if name != "write":
            p.add_argument("--limit", type=int, default=None, help="use only the first LIMIT bytes")
        if name == "hash":
            p.add_argument("--algo", default="blake2b")
            p.add_argument("--start", type=int, default=0)
            p.add_argument("--end", type=int, default=None)
        p.set_defaults(fn=fn)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # the client stopped reading (a sample verify closes the stream early): not a failure; point stdout at
        # /dev/null so the exit-time flush cannot fail the unit with status 120
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        sys.exit(0)
