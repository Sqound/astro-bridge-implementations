FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils util-linux mount parted e2fsprogs \
    python3 qemu-user-static qemu-system-arm kmod dosfstools device-tree-compiler iproute2 nftables conntrack dbus network-manager systemd \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /project
ENTRYPOINT ["bash", "/project/image/customize.sh"]
