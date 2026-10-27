#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
mkdir -p image/cache dist
python3 image/download.py
docker build -t astro-bridge-builder:0.6.0 -f image/Dockerfile .
# All loop devices belong to Docker's Linux VM on macOS. No SD/disk is passed.
docker run --rm --privileged --name astro-bridge-image-build \
  --mount "type=bind,source=$PROJECT_DIR,target=/project" \
  --mount type=volume,source=astro-bridge-build-work,destination=/work \
  astro-bridge-builder:0.6.0
echo "Image and checksums available in $PROJECT_DIR/dist"
