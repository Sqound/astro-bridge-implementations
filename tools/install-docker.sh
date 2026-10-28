#!/bin/sh
# SPDX-License-Identifier: MIT
# Download the checksum-pinned Docker installer; bootstrap Python only on request.
set -eu
apply=false
for arg in "$@"; do [ "$arg" != --apply ] || apply=true; done
if ! command -v python3 >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  if [ "$apply" != true ]; then
    echo 'Python 3 and curl are required. Add --apply to install them on Debian/Ubuntu.'
    exit 0
  fi
  if [ "$(uname -s)" != Linux ] || ! command -v apt-get >/dev/null 2>&1 || [ "$(id -u)" != 0 ]; then
    echo 'Install Python 3 and curl, or run with sudo on Debian/Ubuntu.' >&2
    exit 1
  fi
  apt-get update
  apt-get install -y python3 curl ca-certificates
fi
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT HUP INT TERM
curl -fL https://github.com/Sqound/astro-bridge-implementations/releases/download/v1.0.1/install-docker.py -o "$work/install-docker.py"
python3 - "$work/install-docker.py" '__DOCKER_INSTALLER_SHA256__' <<'PY'
import hashlib,pathlib,sys
assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()==sys.argv[2], 'Docker installer checksum mismatch'
PY
python3 "$work/install-docker.py" "$@"
