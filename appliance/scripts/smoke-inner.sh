#!/bin/bash
# runs inside the smoke-booted guest (injected by smoke.sh); output goes to the serial console
export SYSTEMD_PAGER=cat PAGER=cat
sleep 15
echo "===SMOKE-BEGIN"
echo "-- units"; systemctl is-active ev2ahv nginx firewalld chronyd sshd NetworkManager auditd ev2ahv-boot ev2ahv-blockd.socket ev2ahv-console cloud-init | tr '\n' ' '; echo
systemctl --failed --no-legend
for u in ev2ahv-firstboot nginx; do echo "-- journal $u"; journalctl -u $u -b --no-pager | tail -12; done
echo "-- app unit status"; systemctl status ev2ahv --no-pager 2>&1 | head -12
matchpathcon /run/ev2ahv /var/lib/ev2ahv/tls /opt/ev2ahv/app/web/dist; ls -lZ /run/ev2ahv /var/lib/ev2ahv/tls
echo "-- disk"; lsblk -J | head -8; df -h / | tail -1; sfdisk -l /dev/sda 2>/dev/null | tail -3
curl -sk -o /dev/null -w "index %{http_code}\n" https://localhost/
curl -sk -w "\nappinfo %{http_code}\n" https://localhost/api/v2/appinfo
curl -sk -D- -o /dev/null https://localhost/ | grep -i -E 'HTTP/|strict|content-sec|x-frame'
curl -s -m 3 -o /dev/null -w "port80 %{http_code}\n" http://localhost/
firewall-cmd --list-all | head -8
echo "-- selinux"; getenforce
echo "AVC-COUNT $(ausearch -m avc,user_avc,selinux_err -ts boot 2>/dev/null | grep -c '^type=AVC')"
ausearch -m avc -ts boot 2>/dev/null | grep -o 'avc:.*' | sort | uniq -c | head -30
ausearch -m avc -ts boot 2>/dev/null | grep -o 'avc:.*' | sort -u | head -30
echo "-- cloud-init"; cloud-init status --long 2>&1 | head -5
echo "-- journal warnings"; journalctl -b -p warning --no-pager | grep -v -i segfault | tail -25
echo "-- keys"; ls /etc/ssh/ssh_host_*; stat -c '%U:%G %a %n' /etc/ev2ahv/* /var/lib/ev2ahv/tls/* /var/lib/ev2ahv
echo "-- tui"; /usr/local/sbin/ev2ahv-config --status
echo "===SMOKE-END"
