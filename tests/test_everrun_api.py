"""D6: FT or HA comes from the everRun web API (DOH watch), not from virsh."""
import os, sys, unittest
import xml.etree.ElementTree as ET
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "server")]
from ev2ahv import everrun_api  # noqa: E402
from ev2ahv.everrun_api import EverRun, availability_level  # noqa: E402

# Shape of a supernova <watch/> response (trimmed); the field values here are assumptions, see docs/results.md D6.
WATCH = """<response id='1' status='ok'>
  <supernova id='supernova:o32'><version>8.1.1-285</version></supernova>
  <vm id='vm:o1'><name>ev2ahv-src-rocky9</name><state>running</state><availability>FT</availability></vm>
  <vm id='vm:o2'><name>ev2ahv-src-win22</name><state>stopped</state><availability>ha</availability></vm>
  <vm id='vm:o3'><name>ev2ahv-src-other</name><state>running</state></vm>
  <vm><name>template-without-id</name></vm>
</response>"""


class FakeEverRun(EverRun):
    def __init__(self, *a, **kw):   # no login
        self.calls = []

    def call(self, target, body):
        self.calls.append(target)
        return ET.fromstring(WATCH)


class AvailabilityTest(unittest.TestCase):
    def test_levels(self):
        for text, want in (("FT", "FT"), ("ft", "FT"), ("Fault Tolerant", "FT"), ("fault-tolerant", "FT"),
                           ("HA", "HA"), ("high availability", "HA"), ("High_Availability", "HA"),
                           ("", ""), (None, ""), ("basic", "")):
            self.assertEqual(availability_level(text), want, text)

    def test_availability_from_watch(self):
        e = FakeEverRun()
        self.assertEqual(e.availability(), {"ev2ahv-src-rocky9": "FT", "ev2ahv-src-win22": "HA", "ev2ahv-src-other": ""})
        self.assertEqual(e.calls, ["supernova"])


class FillTest(unittest.TestCase):
    def setUp(self):
        from ev2ahv_server import live
        self.live = live

    def doc(self):
        return {"id": "p1", "type": "everrun", "address": "everrun.example", "username": "admin",
                "workloads": [{"name": "ev2ahv-src-rocky9", "availability": ""},
                              {"name": "ev2ahv-src-new", "availability": ""}]}

    def test_fills_from_api(self):
        d = self.doc()
        self.live._fill_availability(d, lambda k: "pw", api=FakeEverRun)
        self.assertEqual([w["availability"] for w in d["workloads"]], ["FT", ""])

    def test_no_password_leaves_blank_and_does_not_call(self):
        d = self.doc()
        self.live._fill_availability(d, lambda k: None, api=lambda *a, **k: self.fail("called the API"))
        self.assertEqual([w["availability"] for w in d["workloads"]], ["", ""])

    def test_version_is_filled(self):
        """D19: the environment shows the everRun release, from supernova/version."""
        d = self.doc()
        self.live._fill_availability(d, lambda k: "pw", api=FakeEverRun)
        self.assertEqual(d["version"], "everRun 8.1.1-285")
        self.assertEqual(FakeEverRun().system_version(), "8.1.1-285")

    def test_api_error_is_not_fatal(self):
        def broken(*a, **k):
            raise RuntimeError("everRun login failed")
        d = self.doc()
        with self.assertLogs("ev2ahv.live", "WARNING"):
            self.live._fill_availability(d, lambda k: "pw", api=broken)
        self.assertEqual([w["availability"] for w in d["workloads"]], ["", ""])


if __name__ == "__main__":
    unittest.main()
