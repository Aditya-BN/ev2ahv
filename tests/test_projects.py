"""D8a: Prism projects (list, set at create) with a fake Prism transport."""
import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv.credentials import Credentials  # noqa: E402
from ev2ahv.prism import NotOwned, Prism, PrismError  # noqa: E402


class FakePrism(Prism):
    def __init__(self, vm_name="ev2ahv-x", project=None):
        super().__init__(Credentials("h", api_key="k"))
        self.calls, self.vm_name, self.project = [], vm_name, project
        self.projects_all = [{"metadata": {"uuid": f"u{i}"}, "status": {"name": f"P{i:03d}"}} for i in range(150)]

    def _call(self, method, path, body=None, etag=None, base="/api"):
        self.calls.append((method, path, body, base))
        if path == "/projects/list":
            o, n = body["offset"], body["length"]
            return {"entities": self.projects_all[o:o + n], "metadata": {"total_matches": 150}}, {}
        if base == "/api" and method == "GET":                      # v4 VM, for the ownership check
            return {"data": {"name": self.vm_name}}, {"ETag": "e"}
        if method == "GET" and path.startswith("/vms/"):
            md = {"kind": "vm", "uuid": "vm1"}
            if self.project:
                md["project_reference"] = {"kind": "project", "uuid": self.project}
            return {"api_version": "3.1", "metadata": md, "spec": {"name": self.vm_name},
                    "status": {"state": "COMPLETE"}}, {}
        if method == "PUT":
            return {"status": {"execution_context": {"task_uuid": "t1"}}}, {}
        if path == "/tasks/t1":
            return {"status": "SUCCEEDED"}, {}
        raise AssertionError((method, path))


class ProjectsTest(unittest.TestCase):
    def test_list_pages_and_sorts(self):
        p = FakePrism()
        names = [x["name"] for x in p.projects()]
        self.assertEqual(len(names), 150)
        self.assertEqual(names[:2], ["P000", "P001"])
        self.assertEqual(sum(1 for c in p.calls if c[1] == "/projects/list"), 2)
        self.assertEqual(p.project_id("P042"), "u42")
        with self.assertRaises(PrismError):
            p.project_id("nope")

    def test_set_project_puts_metadata_without_status(self):
        p = FakePrism()
        self.assertTrue(p.set_project("vm1", "u7"))
        put = [c for c in p.calls if c[0] == "PUT"][0]
        self.assertEqual(put[2]["metadata"]["project_reference"], {"kind": "project", "uuid": "u7"})
        self.assertNotIn("status", put[2])
        self.assertEqual(put[3], "/api/nutanix/v3")

    def test_already_in_project_is_a_no_op(self):
        p = FakePrism(project="u7")
        self.assertFalse(p.set_project("vm1", "u7"))
        self.assertFalse([c for c in p.calls if c[0] == "PUT"])

    def test_refuses_vms_it_does_not_own(self):
        with self.assertRaises(NotOwned):
            FakePrism(vm_name="prod-db01").set_project("vm1", "u7")


if __name__ == "__main__":
    unittest.main()
