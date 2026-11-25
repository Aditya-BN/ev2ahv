"""Prism credentials: API key or username/password, stored 0600 outside the repo."""
import base64, json, os, re, tempfile

CONFIG_DIR = os.path.expanduser(os.environ.get("EV2AHV_CONFIG_DIR", "~/.config/ev2ahv"))
CRED_FILE = os.path.join(CONFIG_DIR, "credentials.json")
FALLBACK_ENV = os.path.expanduser("~/nutanix/.env")


class Credentials:
    def __init__(self, host, port=9440, api_key=None, username=None, password=None,
                 verify_tls=False, source="unknown"):
        self.host, self.port, self.api_key = host, int(port), api_key
        self.username, self.password = username, password
        self.verify_tls, self.source = verify_tls, source

    @property
    def method(self):
        return "api_key" if self.api_key else "basic"

    def headers(self):
        if self.api_key:
            return {"X-ntnx-api-key": self.api_key}
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def public(self):
        """Description safe to show in logs and the UI: never includes a secret."""
        return {"host": self.host, "port": self.port, "method": self.method,
                "username": self.username if self.method == "basic" else None,
                "verify_tls": self.verify_tls, "source": self.source}


def _parse_env(path):
    out = {}
    with open(path) as fh:
        for line in fh:
            m = re.match(r"^(?:export )?([A-Za-z_]\w*)=(.*)$", line.strip())
            if m:
                out[m[1]] = m[2].strip().strip("\"'")
    return out


def load():
    """Environment variables win (the web server passes each environment's own credentials this way),
    then the saved file, then the existing ~/nutanix/.env."""
    env = os.environ
    if env.get("NTNX_PC") and env.get("NTNX_API_KEY"):
        return Credentials(env["NTNX_PC"], api_key=env["NTNX_API_KEY"], source="env")
    if env.get("NTNX_PC") and env.get("NTNX_USERNAME") and env.get("NTNX_PASSWORD"):
        return Credentials(env["NTNX_PC"], username=env["NTNX_USERNAME"], password=env["NTNX_PASSWORD"], source="env")
    if os.path.exists(CRED_FILE):
        with open(CRED_FILE) as fh:
            d = json.load(fh)
        return Credentials(d["host"], d.get("port", 9440), d.get("api_key"), d.get("username"),
                           d.get("password"), d.get("verify_tls", False), "ev2ahv")
    if os.path.exists(FALLBACK_ENV):
        e = _parse_env(FALLBACK_ENV)
        if e.get("PC_HOST") and e.get("PC_API_KEY"):
            return Credentials(e["PC_HOST"], e.get("PC_PORT", 9440), api_key=e["PC_API_KEY"],
                               verify_tls=e.get("PC_INSECURE", "true").lower() != "true",
                               source="existing-nutanix-env")
    raise RuntimeError("No Prism credentials. Run: ev2ahv creds set")


def save(host, port=9440, api_key=None, username=None, password=None, verify_tls=False):
    if bool(api_key) == bool(username and password):
        raise ValueError("Provide either an API key or a username and password")
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    body = {"host": host, "port": int(port), "verify_tls": verify_tls}
    body.update({"api_key": api_key} if api_key else {"username": username, "password": password})
    fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR)
    with os.fdopen(fd, "w") as fh:
        json.dump(body, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, CRED_FILE)
