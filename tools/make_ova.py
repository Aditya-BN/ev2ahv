"""Build dist/ev2ahv-<version>.ova from dist/ev2ahv-<version>.qcow2 (T7).

  python3 tools/make_ova.py [--version X]

Converts the qcow2 to a stream-optimized VMDK with qemu-img inside the ev2ahv-builder docker image (no sudo),
writes an OVF 1.0 descriptor (2 vCPU, 4 GiB, 1 NIC, 1 disk of the image's virtual size, BIOS), and tars
descriptor, manifest (.mf, SHA256) and disk in that order. Also run by `EV2AHV_OVA=1 bash appliance/build.sh`.
"""
import argparse, hashlib, os, subprocess, sys, tarfile, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OVF = """<?xml version="1.0" encoding="UTF-8"?>
<Envelope vmw:buildId="ev2ahv-{ver}" xmlns="http://schemas.dmtf.org/ovf/envelope/1" xmlns:cim="http://schemas.dmtf.org/wbem/wscim/1/common" xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1" xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData" xmlns:vmw="http://www.vmware.com/schema/ovf" xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <References>
    <File ovf:href="{vmdk}" ovf:id="file1" ovf:size="{vmdk_size}"/>
  </References>
  <DiskSection>
    <Info>Virtual disk information</Info>
    <Disk ovf:capacity="{cap}" ovf:capacityAllocationUnits="byte" ovf:diskId="vmdisk1" ovf:fileRef="file1" ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized"/>
  </DiskSection>
  <NetworkSection>
    <Info>The list of logical networks</Info>
    <Network ovf:name="VM Network"><Description>VM Network</Description></Network>
  </NetworkSection>
  <VirtualSystem ovf:id="ev2ahv-appliance-{ver}">
    <Info>ev2ahv appliance {ver} (Rocky Linux 9)</Info>
    <Name>ev2ahv-appliance-{ver}</Name>
    <OperatingSystemSection ovf:id="107" vmw:osType="rhel9_64Guest">
      <Info>Rocky Linux 9 64-bit</Info>
    </OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual hardware requirements</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemIdentifier>ev2ahv-appliance-{ver}</vssd:VirtualSystemIdentifier>
        <vssd:VirtualSystemType>vmx-13</vssd:VirtualSystemType>
      </System>
      <Item>
        <rasd:AllocationUnits>hertz * 10^6</rasd:AllocationUnits>
        <rasd:Description>Number of Virtual CPUs</rasd:Description>
        <rasd:ElementName>2 virtual CPU(s)</rasd:ElementName>
        <rasd:InstanceID>1</rasd:InstanceID>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>2</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits>
        <rasd:Description>Memory Size</rasd:Description>
        <rasd:ElementName>4096MB of memory</rasd:ElementName>
        <rasd:InstanceID>2</rasd:InstanceID>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>4096</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Address>0</rasd:Address>
        <rasd:Description>SCSI Controller</rasd:Description>
        <rasd:ElementName>SCSI Controller 0</rasd:ElementName>
        <rasd:InstanceID>3</rasd:InstanceID>
        <rasd:ResourceSubType>lsilogic</rasd:ResourceSubType>
        <rasd:ResourceType>6</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:ElementName>Hard Disk 1</rasd:ElementName>
        <rasd:HostResource>ovf:/disk/vmdisk1</rasd:HostResource>
        <rasd:InstanceID>4</rasd:InstanceID>
        <rasd:Parent>3</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>7</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>true</rasd:AutomaticAllocation>
        <rasd:Connection>VM Network</rasd:Connection>
        <rasd:Description>VmxNet3 ethernet adapter on "VM Network"</rasd:Description>
        <rasd:ElementName>Network adapter 1</rasd:ElementName>
        <rasd:InstanceID>5</rasd:InstanceID>
        <rasd:ResourceSubType>VmxNet3</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
      </Item>
    </VirtualHardwareSection>
  </VirtualSystem>
</Envelope>
"""


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default=open(os.path.join(ROOT, "VERSION")).read().strip())
    a = ap.parse_args()
    ver, dist = a.version, os.path.join(ROOT, "dist")
    qcow = os.path.join(dist, f"ev2ahv-{ver}.qcow2")
    if not os.path.exists(qcow):
        sys.exit(f"missing {qcow}; run appliance/build.sh first")
    name = f"ev2ahv-appliance-{ver}"
    with tempfile.TemporaryDirectory(dir=os.path.join(ROOT, "build")) as tmp:
        subprocess.run(["docker", "run", "--rm", "-v", f"{dist}:/d:ro", "-v", f"{tmp}:/o", "ev2ahv-builder", "bash", "-c",
                        f"qemu-img convert -f qcow2 -O vmdk -o subformat=streamOptimized,adapter_type=lsilogic "
                        f"/d/ev2ahv-{ver}.qcow2 /o/{name}-disk1.vmdk && chown {os.getuid()}:{os.getgid()} /o/*"], check=True)
        vmdk = os.path.join(tmp, f"{name}-disk1.vmdk")
        import json
        cap = json.loads(subprocess.check_output(["docker", "run", "--rm", "-v", f"{dist}:/d:ro", "ev2ahv-builder",
                                                  "qemu-img", "info", "--output=json", f"/d/ev2ahv-{ver}.qcow2"]))["virtual-size"]
        ovf = os.path.join(tmp, f"{name}.ovf")
        with open(ovf, "w") as f:
            f.write(OVF.format(ver=ver, vmdk=os.path.basename(vmdk), vmdk_size=os.path.getsize(vmdk), cap=cap))
        mf = os.path.join(tmp, f"{name}.mf")
        with open(mf, "w") as f:
            for p in (ovf, vmdk):
                f.write(f"SHA256({os.path.basename(p)})= {sha256(p)}\n")
        out = os.path.join(dist, f"ev2ahv-{ver}.ova")
        with tarfile.open(out, "w", format=tarfile.USTAR_FORMAT) as t:   # OVF must be the first entry
            for p in (ovf, mf, vmdk):
                ti = t.gettarinfo(p, os.path.basename(p))
                ti.uid = ti.gid = 0; ti.uname = ti.gname = "root"
                with open(p, "rb") as fh:
                    t.addfile(ti, fh)
    with open(out + ".sha256", "w") as f:
        f.write(f"{sha256(out)}  {os.path.basename(out)}\n")
    print(out, os.path.getsize(out))


if __name__ == "__main__":
    main()
