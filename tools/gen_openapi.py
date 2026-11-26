"""Write web/openapi.json from the server models (the API contract), or check that it is current.

python3 tools/gen_openapi.py           # rewrite web/openapi.json
python3 tools/gen_openapi.py --check   # exit 1 if web/openapi.json is out of date (CI)
"""
import json, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
os.environ.setdefault("EV2AHV_DATA", tempfile.mkdtemp(prefix="ev2ahv-openapi-"))
os.environ.setdefault("EV2AHV_SERVER_DB", os.path.join(os.environ["EV2AHV_DATA"], "openapi.db"))
from ev2ahv_server.api import app  # noqa: E402

OUT = os.path.join(ROOT, "web", "openapi.json")
text = json.dumps(app.openapi(), indent=4) + "\n"
if "--check" in sys.argv:
    with open(OUT) as f:
        if f.read() != text:
            sys.exit("web/openapi.json is out of date: run python3 tools/gen_openapi.py")
    print("web/openapi.json is current")
else:
    with open(OUT, "w") as f:
        f.write(text)
    print("wrote", OUT)
