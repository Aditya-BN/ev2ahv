import json, os, stat, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ev2ahv import guest, plan

WIN_OK = {"firmware": "bios", "san_policy": "Online All", "prepared_at": "t",
          "drivers": {"vioscsi": {"boot_ready": True, "sys": True}, "viostor": {"boot_ready": True, "sys": True},
                      "netkvm": {"sys": True, "staged": True}},
          "adapters": [{"mac": "00:e0:09:a4:d7:41", "dhcp": False}],
          "task": {"registered": True, "script": True, "saved_macs": ["00:e0:09:a4:d7:41"]}}
LIN_OK = {"firmware": "bios", "prepared_at": "t", "fstab_bad": [], "crypttab_bad": [], "lvm_devices_file": False,
          "initramfs": {"readable": True, "virtio_scsi": True, "virtio_blk": True, "virtio_pci": True, "virtio_net": True},
          "nics": [{"name": "ens3f0", "mac": "00:e0:09:a4:d7:40", "pinned": True, "nm_by_mac": True, "bound_by_name": True}]}


def statuses(items):
    return {i["name"]: i["status"] for i in items}


class Grading(unittest.TestCase):
    def test_single_adapter_as_bare_object(self):
        f = json.loads(json.dumps(WIN_OK)); f["adapters"] = f["adapters"][0]; f["task"]["saved_macs"] = "00:e0:09:a4:d7:41"
        self.assertEqual(statuses(guest.grade("windows", f, "scsi", "bios"))["Network re-apply"], "PASS")

    def test_prepared_windows_passes(self):
        self.assertNotIn("FAIL", statuses(guest.grade("windows", WIN_OK, "scsi", "bios")).values())

    def test_windows_scsi_without_vioscsi_fails_but_pci_passes(self):
        f = json.loads(json.dumps(WIN_OK)); f["drivers"]["vioscsi"] = {"boot_ready": False}
        self.assertEqual(statuses(guest.grade("windows", f, "scsi", "bios"))["Boot driver vioscsi for disk_bus scsi"], "FAIL")
        s = statuses(guest.grade("windows", f, "pci", "bios"))
        self.assertEqual(s["Boot driver viostor for disk_bus pci"], "PASS")
        self.assertEqual(s["vioscsi staged for SCSI conversion"], "WARN")

    def test_windows_staged_but_unbound_vioscsi_fails_scsi(self):
        f = json.loads(json.dumps(WIN_OK)); f["drivers"]["vioscsi"] = {"boot_ready": False, "staged": True, "bound": False, "sys": True}
        item = [i for i in guest.grade("windows", f, "scsi", "bios") if i["name"].startswith("Boot driver")][0]
        self.assertEqual(item["status"], "FAIL")
        self.assertIn("disk_bus: pci", item["detail"])

    def test_pci_plan_reports_vioscsi_staging_for_conversion(self):
        f = json.loads(json.dumps(WIN_OK)); f["drivers"]["vioscsi"] = {"staged": False, "bound": False}
        self.assertEqual(statuses(guest.grade("windows", f, "pci", "bios"))["vioscsi staged for SCSI conversion"], "WARN")
        f["drivers"]["vioscsi"]["staged"] = True
        self.assertEqual(statuses(guest.grade("windows", f, "pci", "bios"))["vioscsi staged for SCSI conversion"], "PASS")

    def test_conversion_default_and_opt_out(self):
        base = {"name": "x", "writer": {"vm": "w", "host": "h"}, "source": {"type": "local"},
                "target": {"vm_name": "ev2ahv-x", "cluster": "c", "container": "k", "disk_bus": "pci"}, "disks": [{"path": "/a"}]}
        from ev2ahv.workflow import Migration
        m = Migration.__new__(Migration); m.p = plan.validate(json.loads(json.dumps(base)))
        m.state = type("S", (), {"get": lambda s, k, d=None: None})()
        self.assertTrue(m.wants_conversion())
        base["target"]["convert_to_scsi"] = False
        m.p = plan.validate(base)
        self.assertFalse(m.wants_conversion())

    def test_windows_static_ip_without_task_fails(self):
        f = json.loads(json.dumps(WIN_OK)); f["task"] = {"registered": False, "saved_macs": []}
        self.assertEqual(statuses(guest.grade("windows", f, "scsi", "bios"))["Network re-apply"], "FAIL")
        f["adapters"][0]["dhcp"] = True
        self.assertEqual(statuses(guest.grade("windows", f, "scsi", "bios"))["Network re-apply"], "PASS")

    def test_firmware_mismatch_fails(self):
        self.assertEqual(statuses(guest.grade("windows", WIN_OK, "scsi", "uefi"))["Firmware"], "FAIL")
        self.assertEqual(statuses(guest.grade("linux", LIN_OK, "scsi", "uefi"))["Firmware"], "FAIL")

    def test_linux_prepared_passes(self):
        self.assertNotIn("FAIL", statuses(guest.grade("linux", LIN_OK, "scsi", "bios")).values())

    def test_linux_nic_bound_by_name_fails_until_pinned(self):
        f = json.loads(json.dumps(LIN_OK)); f["nics"][0].update(pinned=False, nm_by_mac=False)
        self.assertEqual(statuses(guest.grade("linux", f, "scsi", "bios"))["NIC ens3f0 00:e0:09:a4:d7:40"], "FAIL")

    def test_linux_missing_boot_driver_and_fstab(self):
        f = json.loads(json.dumps(LIN_OK)); f["initramfs"]["virtio_scsi"] = False; f["fstab_bad"] = ["/dev/vdb1"]
        s = statuses(guest.grade("linux", f, "scsi", "bios"))
        self.assertEqual((s["Boot driver virtio_scsi for disk_bus scsi"], s["fstab references"]), ("FAIL", "FAIL"))
        self.assertEqual(statuses(guest.grade("linux", f, "pci", "bios"))["Boot driver virtio_blk for disk_bus pci"], "PASS")

    def test_unprepared_is_a_warning(self):
        f = dict(LIN_OK, prepared_at="")
        self.assertEqual(statuses(guest.grade("linux", f, "scsi", "bios"))["Prepared"], "WARN")

    def test_facts_marker_parsing(self):
        self.assertEqual(guest.parse_facts('noise\nEV2AHV-JSON:{"a": 1}\n')["a"], 1)
        with self.assertRaises(RuntimeError):
            guest.parse_facts("no facts here")


class Secrets(unittest.TestCase):
    def test_password_from_env_then_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.env")
            with open(f, "w") as fh:
                fh.write("OTHER=x\nWINPW=from-file\n")
            os.chmod(f, 0o600)
            os.environ.pop("WINPW", None)
            self.assertEqual(guest.secret({"password_env": "WINPW", "password_file": f}), "from-file")
            os.environ["WINPW"] = "from-env"
            try:
                self.assertEqual(guest.secret({"password_env": "WINPW", "password_file": f}), "from-env")
            finally:
                del os.environ["WINPW"]

    def test_world_readable_secret_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "s.txt")
            with open(f, "w") as fh:
                fh.write("pw\n")
            os.chmod(f, 0o644)
            with self.assertRaises(RuntimeError):
                guest.secret({"password_file": f})

    def test_plan_rejects_inline_password(self):
        base = {"name": "x", "writer": {"vm": "w", "host": "h"}, "source": {"type": "local"},
                "target": {"vm_name": "ev2ahv-x", "cluster": "c", "container": "k"}, "disks": [{"path": "/tmp/a"}]}
        with self.assertRaises(plan.PlanError):
            plan.validate(dict(base, guest={"os": "windows", "prep": "auto", "address": "1.2.3.4", "password": "x"}))
        with self.assertRaises(plan.PlanError):
            plan.validate(dict(base, guest={"os": "windows", "prep": "auto", "address": "1.2.3.4"}))
        ok = plan.validate(dict(base, guest={"os": "linux", "prep": "auto", "address": "1.2.3.4"}))
        self.assertEqual(ok["guest"]["prep"], "auto")


class Gating(unittest.TestCase):
    def test_cutover_refuses_on_fail_unless_forced(self):
        from ev2ahv.workflow import Migration
        m = Migration.__new__(Migration)
        kv = {"staging": {"d": {}}}
        class S:
            def get(self, k, d=None): return kv.get(k, d)
            def set(self, k, v): kv[k] = v
            def event(self, *a, **k): kv.setdefault("events", []).append(a)
        m.state, m.dry_run, m.lock = S(), False, lambda: None
        m.p = {"name": "x", "target": {"vm_name": "ev2ahv-x"}, "disks": []}
        m.prep_check = lambda: [{"name": "Guest: Boot driver", "status": "FAIL", "detail": "missing"}]
        stopped = []
        m.stop_source = lambda *a: stopped.append(True)
        with self.assertRaises(RuntimeError) as e:
            m.cutover()
        self.assertIn("did not start", str(e.exception))
        self.assertEqual(stopped, [])
        class P:
            def vm_by_name(self, n): return None
        m.prism = P()
        m.source_stopped = lambda: True
        try:
            m.cutover(force=True)
        except Exception:
            pass      # later steps need a cluster; we only check that force got past the gate
        self.assertEqual(stopped, [True])


if __name__ == "__main__":
    unittest.main()


class DirectScsi(unittest.TestCase):
    """D9: a Windows guest whose vioscsi is boot-ready is created on SCSI and skips the PCI to SCSI conversion."""

    BASE = {"name": "x", "writer": {"vm": "w", "host": "h"}, "source": {"type": "local"},
            "target": {"vm_name": "ev2ahv-x", "cluster": "c", "container": "k", "disk_bus": "pci"},
            "disks": [{"path": "/a"}, {"path": "/b"}], "guest": {"os": "windows", "prep": "auto", "address": "1.2.3.4",
                                                               "password_env": "PW"}}

    def mig(self, facts, **target):
        from ev2ahv.workflow import Migration
        base = json.loads(json.dumps(self.BASE)); base["target"].update(dict({"try_direct_scsi": True}, **target))
        m = Migration.__new__(Migration); m.p = plan.validate(base); m._guest_facts = facts
        stored = {}
        m.state = type("S", (), {"get": lambda s, k, d=None: stored.get(k, d), "set": lambda s, k, v: stored.__setitem__(k, v),
                                  "event": lambda *a, **k: None})()
        return m

    def test_direct_scsi_is_opt_in(self):
        """Off unless target.try_direct_scsi: the direct boot failed on Server 2022 in the lab (2026-11-27)."""
        self.assertFalse(self.mig(WIN_OK, try_direct_scsi=False).direct_scsi_now())
        self.assertTrue(self.mig(WIN_OK).direct_scsi_now())

    def test_grade_row_pass_when_boot_ready_warn_when_not(self):
        name = "Direct SCSI boot (vioscsi boot-ready)"
        self.assertEqual(statuses(guest.grade("windows", WIN_OK, "pci", "bios"))[name], "PASS")
        f = json.loads(json.dumps(WIN_OK)); f["drivers"]["vioscsi"] = {"boot_ready": False, "staged": True, "bound": False}
        item = [i for i in guest.grade("windows", f, "pci", "bios") if i["name"] == name][0]
        self.assertEqual(item["status"], "WARN")
        self.assertIn("converted to SCSI", item["detail"])
        self.assertNotIn(name, statuses(guest.grade("windows", WIN_OK, "scsi", "bios")))

    def test_scsi_boot_ready(self):
        self.assertTrue(guest.scsi_boot_ready(WIN_OK))
        self.assertFalse(guest.scsi_boot_ready({"drivers": {"vioscsi": {"boot_ready": False}}}))
        self.assertFalse(guest.scsi_boot_ready({}))
        self.assertFalse(guest.scsi_boot_ready(None))

    def test_decision_follows_the_guest_facts(self):
        self.assertTrue(self.mig(WIN_OK).direct_scsi_now())
        self.assertFalse(self.mig(None).direct_scsi_now())
        f = json.loads(json.dumps(WIN_OK)); f["drivers"]["vioscsi"]["boot_ready"] = False
        self.assertFalse(self.mig(f).direct_scsi_now())
        self.assertFalse(self.mig(WIN_OK, convert_to_scsi=False).direct_scsi_now())
        lin = self.mig(WIN_OK); lin.p["guest"]["os"] = "linux"
        self.assertFalse(lin.direct_scsi_now())

    def test_direct_skips_conversion_and_builds_scsi_disks(self):
        from ev2ahv import builder
        m = self.mig(WIN_OK)
        self.assertTrue(m.wants_conversion())          # nothing decided yet: the old path
        self.assertEqual(m.create_plan()["target"]["disk_bus"], "pci")
        m.state.set("direct_scsi", m.direct_scsi_now())
        self.assertFalse(m.wants_conversion())
        body = builder.vm_payload(m.create_plan(), "cl", [(1024 ** 2, "r1"), (1024 ** 2, "r2")], {})
        self.assertEqual([d["diskAddress"] for d in body["disks"]],
                         [{"busType": "SCSI", "index": 0}, {"busType": "SCSI", "index": 1}])
        self.assertEqual(body["bootConfig"]["bootDevice"]["diskAddress"], {"busType": "SCSI", "index": 0})
        self.assertEqual(m.p["target"]["disk_bus"], "pci")   # the plan itself is not changed

    def test_not_ready_keeps_pci_and_conversion(self):
        from ev2ahv import builder
        f = json.loads(json.dumps(WIN_OK)); f["drivers"]["vioscsi"]["boot_ready"] = False
        m = self.mig(f); m.state.set("direct_scsi", m.direct_scsi_now())
        self.assertTrue(m.wants_conversion())
        body = builder.vm_payload(m.create_plan(), "cl", [(1024 ** 2, "r1"), (1024 ** 2, "r2")], {})
        self.assertEqual({d["diskAddress"]["busType"] for d in body["disks"]}, {"PCI"})

    def test_failed_direct_boot_moves_back_to_pci_and_converts_later(self):
        m = self.mig(WIN_OK); m.state.set("direct_scsi", True)
        calls = []
        class P:
            def power(s, vm, how): calls.append(("power", how))
            def wait_power(s, vm, st, *a): calls.append(("wait", st))
            def set_boot_disk(s, vm, bus, i): calls.append(("boot", bus, i))
        m.prism = P(); m._bus_disks = lambda vm, bus: [1, 2]
        m._move_disks = lambda vm, a, b, i: calls.append(("move", a, b, i))
        m.verify = lambda: {"power": "ON"}
        v = m.revert_direct_scsi("vm", RuntimeError("no boot"))
        self.assertEqual(v, {"power": "ON"})
        self.assertIn(("move", "SCSI", "PCI", 0), calls)
        self.assertFalse(m.direct_scsi())
        self.assertEqual(m.state.get("conversion")["result"], "direct SCSI reverted to PCI")
