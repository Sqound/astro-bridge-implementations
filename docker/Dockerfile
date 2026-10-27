# syntax=docker/dockerfile:1
ARG BASE_IMAGE=debian@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
FROM ${BASE_IMAGE} AS common
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY core.lock.json /opt/astro-bridge/core.lock.json
COPY core.whl /tmp/core.whl
RUN python3 -c 'import hashlib,json,zipfile; p="/tmp/core.whl"; assert hashlib.sha256(open(p,"rb").read()).hexdigest()==json.load(open("/opt/astro-bridge/core.lock.json"))["sha256"], "Core checksum mismatch"; zipfile.ZipFile(p).extractall("/opt/astro-bridge")' && rm /tmp/core.whl
ENV PYTHONPATH=/opt/astro-bridge PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ASTRO_BRIDGE_PLATFORM=docker
WORKDIR /opt/astro-bridge
EXPOSE 8787
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s CMD python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8787/",timeout=2).read()' || exit 1
ENTRYPOINT ["python3", "-m", "wifi_bridge"]

FROM common AS demo
RUN install -d -m 0700 -o 10001 -g 10001 /var/lib/wifi-bridge
USER 10001:10001
VOLUME ["/var/lib/wifi-bridge"]
CMD ["--demo", "--bind", "0.0.0.0", "--port", "8787", "--state-dir", "/var/lib/wifi-bridge"]

FROM common AS conntrack-build
RUN apt-get update && apt-get install -y --no-install-recommends build-essential pkg-config libmnl-dev libnetfilter-conntrack-dev libnfnetlink-dev libtirpc-dev bison flex && rm -rf /var/lib/apt/lists/*
COPY build-conntrack.py /tmp/build-conntrack.py
RUN python3 /tmp/build-conntrack.py --prefix /opt/conntrack

FROM common AS linux
RUN apt-get update && apt-get install -y --no-install-recommends network-manager systemd dbus hostapd dnsmasq-base nftables conntrack iw iproute2 rfkill openssl chrony python3-venv && rm -rf /var/lib/apt/lists/*
# Full runtime adds Web Push libraries in an isolated venv, never the host Python.
RUN python3 -m venv /opt/push && /opt/push/bin/pip install --no-cache-dir py-vapid==1.9.6 http-ece==1.2.1 'cryptography>=46,<51'
COPY --from=conntrack-build /opt/conntrack /opt/conntrack
COPY tls.py /usr/libexec/astro-bridge-tls
RUN install -d -m 700 /etc/wifi-bridge /var/lib/wifi-bridge && chmod 755 /usr/libexec/astro-bridge-tls && printf '2\n' > /etc/wifi-bridge/tls-helper-version
ENV ASTRO_BRIDGE_CONNTRACK=/opt/conntrack/sbin/conntrack
ENV PYTHONPATH=/opt/astro-bridge
COPY entrypoint.py /opt/astro-bridge/entrypoint.py
ENTRYPOINT ["/opt/push/bin/python", "/opt/astro-bridge/entrypoint.py"]
EXPOSE 80 443
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s CMD python3 -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1/",timeout=2).read()' || exit 1
CMD ["--bind", "0.0.0.0", "--port", "80", "--allow-host", "localhost", "--allow-host", "127.0.0.1"]
