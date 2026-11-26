#!/bin/bash
# Security scans of the image root tree (build/tree, produced by mkimage.sh). Output in build/scan/.
set -uo pipefail
cd "$(dirname "$0")/../.."
REPO=$PWD; mkdir -p build/scan build/trivy-cache
# One trivy DB cache for both runs: the second run skips the DB update and failed on a cold cache before.
TC="-v $REPO/build/trivy-cache:/root/.cache/trivy"
docker run --rm $TC -v $REPO/build/tree:/t:ro -v $REPO/build/scan:/o aquasec/trivy:latest rootfs --scanners vuln --severity HIGH,CRITICAL --format json -o /o/trivy.json /t >/dev/null 2>&1
docker run --rm $TC -v $REPO/build/tree:/t:ro aquasec/trivy:latest rootfs --scanners vuln --severity HIGH,CRITICAL --skip-db-update /t 2>&1 | tail -40 > build/scan/trivy.txt
docker run --rm -v $REPO/appliance:/a:ro ev2ahv-builder bash -c 'python3.12 -m pip install -q -U pip pip-audit >/dev/null 2>&1; python3.12 -m pip_audit -r /a/requirements.txt --no-deps --disable-pip 2>&1 | tail -30' > build/scan/pip-audit.txt
docker run --rm --privileged -v $REPO/build/tree:/t -v $REPO/build/scan:/o ev2ahv-builder bash -c '
 dnf -y -q install openscap-scanner scap-security-guide >/dev/null 2>&1
 ls /usr/share/xml/scap/ssg/content/ | head -20
 DS=$(ls /usr/share/xml/scap/ssg/content/ssg-rl9-ds.xml 2>/dev/null || ls /usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml)
 echo "datastream: $DS"
 mkdir -p /t/proc /t/sys; mount -t proc proc /t/proc
 oscap-chroot /t xccdf eval --profile xccdf_org.ssgproject.content_profile_cis_server_l1 --results /o/oscap-results.xml --report /o/oscap-report.html $DS > /o/oscap.txt 2>&1
 oscap xccdf eval --skip-valid --profile xccdf_org.ssgproject.content_profile_cis_server_l1 --results /o/oscap-results.xml --report /o/oscap-report.html $DS >/dev/null 2>&1 || true
 umount /t/proc
 grep -c "^Result.*pass" /o/oscap.txt; grep -c "^Result.*fail" /o/oscap.txt; tail -3 /o/oscap.txt'
