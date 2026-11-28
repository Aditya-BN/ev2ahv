"""D29: map each source NIC of a VM to its own target subnet. Pure logic, shared by the API (start check) and the
live engine plan builder."""


class NicMapError(ValueError):
    pass


def resolve_nic_subnets(nics, network_map, overrides=None):
    """D29: pick the target subnet of every NIC. nics: [{"network": source network or None}] in NIC order.
    Order of precedence: the per-VM override (keyed by NIC index as text), then the network_map entry of the NIC's
    source network, then, for old plans (one mapping, or a source that has no network names), the first mapping for
    every NIC with a warning. Returns ([(subnet, test_subnet)], [warning]); raises NicMapError naming each unmapped NIC."""
    overrides, nm = overrides or {}, network_map or []
    by_net = {m["source_network"]: m for m in nm}
    out, warns, missing = [], [], []
    for i, nic in enumerate(nics):
        net = nic.get("network")
        entry = by_net.get(net)
        if overrides.get(str(i)):
            out.append((overrides[str(i)], (entry or {}).get("test_subnet")))
        elif entry:
            out.append((entry["target_subnet"], entry.get("test_subnet")))
        elif nm and (len(nm) == 1 or not net):
            out.append((nm[0]["target_subnet"], nm[0].get("test_subnet")))
            if len(nics) > 1:
                warns.append(f"NIC {i} ({net or 'unnamed network'}) has no mapping of its own: using {nm[0]['target_subnet']}, "
                             "the first mapped subnet, as older plans did. Map each network (step 3) or set the NIC in VM settings")
        else:
            out.append((None, None))
            missing.append(f"NIC {i} ({net or 'unnamed network'}{', ' + nic['mac'] if nic.get('mac') else ''})")
    if missing:
        raise NicMapError("No target subnet for " + "; ".join(missing)
                        + ". Map its source network in Network and Policy, or set the NIC in VM settings")
    return out, warns
