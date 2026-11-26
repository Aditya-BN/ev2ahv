FROM rockylinux:9
RUN dnf -y -q install --setopt=install_weak_deps=False dnf-utils xfsprogs dosfstools e2fsprogs gdisk util-linux \
      grub2-tools grub2-pc grub2-pc-modules policycoreutils checkpolicy qemu-img rsync tar gzip openssl kpartx \
      python3 python3-pip python3.12 python3.12-pip && dnf clean all
