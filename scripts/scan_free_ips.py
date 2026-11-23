#!/usr/bin/env python3
"""Find unused IPs in a /24: Prism NICs, two ICMP sweeps, TCP probe, reverse DNS."""
import concurrent.futures as cf, ipaddress, json, os, re, socket, ssl, subprocess, sys, time, urllib.request

def env(path):
    out = {}
    for line in open(path):
        m = re.match(r'^(?:export )?([A-Za-z_]\w*)=(.*)$', line.strip())
        if m: out[m[1]] = m[2].strip().strip('"\'')
    return out

def prism_ips(cfg, net):
    ctx = ssl._create_unverified_context()
    found, page = {}, 0
    while True:
        url = f"https://{cfg['PC_HOST']}:{cfg.get('PC_PORT','9440')}/api/vmm/v4.2/ahv/config/vms?$page={page}&$limit=100&$select=name,nics"
        req = urllib.request.Request(url.replace(' ', '%20'), headers={'X-ntnx-api-key': cfg['PC_API_KEY']})
        data = json.load(urllib.request.urlopen(req, context=ctx, timeout=60))
        vms = data.get('data') or []
        for vm in vms:
            for ip in re.findall(r'"value": ?"(\d+\.\d+\.\d+\.\d+)"', json.dumps(vm.get('nics') or [])):
                if ipaddress.ip_address(ip) in net:
                    found[ip] = vm.get('name')
        if len(vms) < 100: return found
        page += 1

def ping(ip):
    return subprocess.run(['ping', '-c', '1', '-W', '1', ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def tcp(ip):
    for port in (22, 80, 135, 443, 445, 3389, 8080, 9440):
        try:
            with socket.create_connection((ip, port), 0.4): return True
        except ConnectionRefusedError: return True
        except OSError: pass
    return False

def ptr(ip):
    try: return socket.gethostbyaddr(ip)[0]
    except OSError: return None

net = ipaddress.ip_network(sys.argv[1])
cfg = env(os.path.expanduser('~/nutanix/.env'))
hosts = [str(h) for h in net.hosts()]
used = {ip: f'prism:{n}' for ip, n in prism_ips(cfg, net).items()}
with cf.ThreadPoolExecutor(64) as ex:
    for sweep in (1, 2):
        for ip, up in zip(hosts, ex.map(ping, hosts)):
            if up: used.setdefault(ip, f'icmp{sweep}')
        time.sleep(2)
    rest = [h for h in hosts if h not in used]
    for ip, up in zip(rest, ex.map(tcp, rest)):
        if up: used.setdefault(ip, 'tcp')
    rest = [h for h in hosts if h not in used]
    for ip, name in zip(rest, ex.map(ptr, rest)):
        if name: used.setdefault(ip, f'ptr:{name}')
free = [h for h in hosts if h not in used]
print(json.dumps({'used': dict(sorted(used.items(), key=lambda kv: ipaddress.ip_address(kv[0]))), 'free_count': len(free), 'free': free}))
