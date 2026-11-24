#!/usr/bin/env python3
"""Save a screenshot of an ev2ahv-* VM's AHV console (read-only: sends no keys).

Usage: .venv/bin/python tools/console_shot.py <vm-name> <out.png>
Speaks RFB over Prism Central's VNC websocket proxy, using ev2ahv's Prism credentials.
"""
import os, ssl, struct, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import websocket
from PIL import Image
from ev2ahv import credentials
from ev2ahv.prism import Prism

name, out = sys.argv[1], sys.argv[2]
if not name.startswith("ev2ahv-"):
    sys.exit(f"Refusing: {name} is not an ev2ahv VM")
c = credentials.load()
p = Prism(c)
vm = p.vm_by_name(name) or sys.exit(f"{name} not found")
ws = websocket.create_connection(
    f"wss://{c.host}:{c.port}/vnc/vm/{vm['extId']}/proxy?proxyClusterUuid={vm['cluster']['extId']}",
    timeout=20, subprotocols=["binary"], sslopt={"cert_reqs": ssl.CERT_NONE},
    header=[f"{k}: {v}" for k, v in c.headers().items()])
buf = b""
def read(n):
    global buf
    while len(buf) < n:
        d = ws.recv(); buf += d.encode("latin-1") if isinstance(d, str) else d
    r, buf = buf[:n], buf[n:]; return r
if not read(12).startswith(b"RFB"):
    sys.exit("not an RFB server")
ws.send_binary(b"RFB 003.008\n")
if 1 not in read(read(1)[0]):
    sys.exit("VNC auth required")
ws.send_binary(b"\x01")
if struct.unpack(">I", read(4))[0]:
    sys.exit("VNC handshake failed")
ws.send_binary(b"\x01")
w, h = struct.unpack(">HH", read(4)); read(16); read(struct.unpack(">I", read(4))[0])
ws.send_binary(b"\x00xxx" + struct.pack(">BBBBHHHBBBxxx", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
ws.send_binary(struct.pack(">BxHi", 2, 1, 0))
ws.send_binary(struct.pack(">BBHHHH", 3, 0, 0, 0, w, h))
img = Image.new("RGB", (w, h))
while True:
    kind = read(1)[0]
    if kind == 0:
        break
    if kind == 1:
        read(3); read(struct.unpack(">H", read(2))[0] * 6)
    elif kind == 3:
        read(3); read(struct.unpack(">I", read(4))[0])
    elif kind != 2:
        sys.exit(f"unexpected RFB message {kind}")
read(1)
for _ in range(struct.unpack(">H", read(2))[0]):
    x, y, rw, rh, enc = struct.unpack(">HHHHi", read(12))
    if enc != 0:
        sys.exit(f"unexpected encoding {enc}")
    img.paste(Image.frombuffer("RGBA", (rw, rh), read(rw * rh * 4), "raw", "BGRA", 0, 1).convert("RGB"), (x, y))
img.save(out)
print(f"{out}: {img.width}x{img.height}")
