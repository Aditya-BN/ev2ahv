"""D14: image fallback space check and qcow2 cleanup on the writer."""
import os, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv.state import State  # noqa: E402
from ev2ahv.workflow import Migration, image_space_needed  # noqa: E402

G = 1 << 30


class FakeWriter:
    def __init__(self, cat_ok=True):
        self.cmds, self.cat_ok = [], cat_ok

    def run(self, cmd, timeout=None):
        self.cmds.append(cmd)
        return "1024\n" if cmd.startswith("stat ") else ""

    def argv(self, cmd):
        return ["true"] if self.cat_ok else ["false"]


class FakePrism:
    def __init__(self, fail=False):
        self.fail, self.deleted, self.uploaded = fail, [], []

    def cluster_id(self, name):
        return "c1"

    def image_by_name(self, name):
        return None

    def delete_image(self, ext_id):
        self.deleted.append(ext_id)

    def upload_image(self, name, stream, size, cluster, desc):
        stream.read()
        if self.fail:
            raise RuntimeError("Prism upload failed")
        self.uploaded.append(name)
        return f"img-{name}"


def migration(writer, prism, state):
    m = Migration.__new__(Migration)   # no transports: only upload_images is exercised
    m.p = {"name": "t", "target": {"cluster": "c", "transfer": "image"}, "disks": [{"name": "vda"}, {"name": "vdb"}]}
    m._writer, m.prism, m.state = writer, prism, state
    state.set("staging", {d: {"file": f"/s/t-{d}.raw", "path": f"/s/t-{d}.raw", "size": G} for d in ("vda", "vdb")})
    return m


class SpaceTest(unittest.TestCase):
    def test_raw_files_plus_largest_disk(self):
        self.assertEqual(image_space_needed([40 * G, 10 * G, 10 * G]), 100 * G)   # was 120 GiB (twice the total)
        self.assertEqual(image_space_needed([10 * G]), 20 * G)
        self.assertEqual(image_space_needed([]), 0)


class UploadCleanupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(os.path.join(self.tmp.name, "s.db"))

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def removed(self, w, disk):
        return [c for c in w.cmds if c.startswith(f"rm -f /s/t-{disk}.qcow2 /s/t-{disk}.qcow2.tmp")]

    def test_success_converts_one_at_a_time_and_removes_each_qcow2(self):
        w, p = FakeWriter(), FakePrism()
        migration(w, p, self.state).upload_images()
        self.assertEqual(p.uploaded, ["ev2ahv-img-t-vda", "ev2ahv-img-t-vdb"])
        self.assertEqual(self.state.get("images"), {"vda": "img-ev2ahv-img-t-vda", "vdb": "img-ev2ahv-img-t-vdb"})
        order = [i for i, c in enumerate(w.cmds) if "qemu-img convert" in c or c.startswith("rm -f /s/t-vda.qcow2 ")]
        self.assertTrue(self.removed(w, "vda") and self.removed(w, "vdb"))
        convert_vdb = next(i for i, c in enumerate(w.cmds) if "qemu-img convert" in c and "vdb" in c)
        last_rm_vda = max(i for i, c in enumerate(w.cmds) if c.startswith("rm -f /s/t-vda.qcow2 "))
        self.assertLess(last_rm_vda, convert_vdb, order)

    def test_failed_upload_still_removes_the_qcow2(self):
        w, p = FakeWriter(), FakePrism(fail=True)
        with self.assertRaises(RuntimeError):
            migration(w, p, self.state).upload_images()
        self.assertEqual(w.cmds[-1], "rm -f /s/t-vda.qcow2 /s/t-vda.qcow2.tmp")

    def test_failed_read_deletes_the_image_and_the_qcow2(self):
        w, p = FakeWriter(cat_ok=False), FakePrism()
        with self.assertRaises(RuntimeError):
            migration(w, p, self.state).upload_images()
        self.assertEqual(p.deleted, ["img-ev2ahv-img-t-vda"])
        self.assertEqual(w.cmds[-1], "rm -f /s/t-vda.qcow2 /s/t-vda.qcow2.tmp")


if __name__ == "__main__":
    unittest.main()
