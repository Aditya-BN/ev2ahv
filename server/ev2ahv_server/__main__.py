"""python -m ev2ahv_server [--mock|--live] [--host 0.0.0.0] [--port 8443]. HTTPS with a self-signed cert."""
import argparse, os, subprocess

TLS = os.path.expanduser("~/.config/ev2ahv/tls")


def cert():
    crt, key = os.path.join(TLS, "server.crt"), os.path.join(TLS, "server.key")
    if not os.path.exists(crt):
        os.makedirs(TLS, mode=0o700, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-keyout", key,
                        "-out", crt, "-subj", "/CN=ev2ahv", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                       check=True, capture_output=True)
        os.chmod(key, 0o600)
    return crt, key


def main():
    ap = argparse.ArgumentParser(prog="ev2ahv_server")
    ap.add_argument("--host", default=os.environ.get("EV2AHV_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("EV2AHV_PORT", 8443)))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--mock", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--http", action="store_true", help="plain HTTP (local testing only)")
    a = ap.parse_args()
    if a.mock or a.live:
        os.environ["EV2AHV_SERVER_MODE"] = "live" if a.live else "mock"
    import uvicorn
    kw = {}
    if not a.http:
        kw["ssl_certfile"], kw["ssl_keyfile"] = cert()
    uvicorn.run("ev2ahv_server.api:app", host=a.host, port=a.port, log_level="info", **kw)


if __name__ == "__main__":
    main()
