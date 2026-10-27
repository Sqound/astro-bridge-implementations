#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 image/alpine/download.py
docker build -t astro-bridge-builder:0.6.0 -f image/Dockerfile .
docker run --rm --privileged --name astro-bridge-alpine-build \
 --mount "type=bind,source=$PWD,target=/project" \
 --mount type=volume,source=astro-bridge-alpine-work,destination=/work \
 --entrypoint bash astro-bridge-builder:0.6.0 /project/image/alpine/customize.sh
