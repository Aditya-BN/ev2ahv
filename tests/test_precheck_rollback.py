"""After a rollback the plan's own earlier AHV copy is still there (OFF); prep-check must not FAIL on it, or the UI
refuses the re-cutover. Any other VM with the target name still fails."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv.state import State  # noqa: E402
from ev2ahv.workflow import Migration  # noqa: E402


class FakePrism:
    def __init__(self, ext):
        self.ext = ext

    def vm_by_name(self, name):
        return {"extId": self.ext, "name": name} if self.ext else None


def target_check(prism, state):
    m = Migration.__new__(Migration)
    m.p, m.prism, m.state = {"target": {"vm_name": "ev2ahv-x"}}, prism, state
    t = m.p["target"]

    # the same body as prep_check's target_free, read from the source so the test follows the code
    import inspect, textwrap
    src = inspect.getsource(Migration.prep_check)
    body = src[src.index("        def target_free():"):src.index('        check("Target VM name", target_free)')]
    ns = {"self": m, "t": t}
    exec(textwrap.dedent(body), ns)
    return ns["target_free"]()


class PrecheckAfterRollbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(os.path.join(self.tmp.name, "s.db"))

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_own_rolled_back_copy_passes(self):
        self.state.set("target_vm", "vm-1")
        self.assertIn("rolled-back", target_check(FakePrism("vm-1"), self.state))

    def test_someone_elses_vm_fails(self):
        self.state.set("target_vm", "vm-1")
        with self.assertRaises(RuntimeError):
            target_check(FakePrism("vm-other"), self.state)
        with self.assertRaises(RuntimeError):
            target_check(FakePrism("vm-2"), State(os.path.join(self.tmp.name, "empty.db")))

    def test_free_name(self):
        self.assertEqual(target_check(FakePrism(None), self.state), "free")


if __name__ == "__main__":
    unittest.main()
