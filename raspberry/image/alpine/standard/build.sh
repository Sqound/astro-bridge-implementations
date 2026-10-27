#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
[ "$#" = 2 ] && [ "$1" = --output ] || { echo 'Usage: image/alpine/standard/build.sh --output NEW_DEDICATED_DIRECTORY' >&2; exit 2; }
output="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).absolute())' "$2")"
snapshot="$(python3 "$ROOT/image/alpine/standard/candidate.py" stage)"
volume="astro-standard-$(date -u +%Y%m%d%H%M%S)-$$"
created=0
cleanup() {
  rm -rf "$snapshot" # Only this run's public tracked-source staging tree.
  if [ "$created" = 1 ]; then
    if [ "${completed:-0}" = 1 ]; then docker volume rm "$volume" >/dev/null
    else echo "Failed job retained for review: volume $volume, output $output" >&2; fi
  fi
}
trap cleanup EXIT
python3 "$ROOT/image/alpine/standard/candidate.py" prepare "$output" "$snapshot"
base="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["container"])' "$snapshot/image/alpine/standard/inputs.json")"
# Pull/inspect only the reviewed immutable OCI index; selected platform recorded.
docker pull "$base"
docker image inspect "$base" --format '{{json .}}' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k:d[k] for k in ("Id","RepoDigests","Architecture","Os")},indent=2))' > "$output/container.json"
! docker volume inspect "$volume" >/dev/null 2>&1 || { echo 'Refusing an existing work volume' >&2; exit 1; }
docker volume create "$volume" >/dev/null
created=1
# No host device or Docker socket mount. Privileged loop access is constrained
# again inside the script by exact image backing file/offset/size before mkfs.
docker run --rm --privileged --name "$volume" \
  --mount "type=bind,source=$snapshot,target=/project,readonly" \
  --mount "type=bind,source=$output,target=/out" \
  --mount "type=volume,source=$volume,destination=/work" \
  --entrypoint /bin/sh "$base" /project/image/alpine/standard/build-inside.sh \
  2>&1 | tee "$output/build.log"
python3 "$ROOT/image/alpine/standard/candidate.py" verify-image \
  "$output/$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["asset"])' "$output/alpine-standard-manifest.json")" \
  "$output/alpine-standard-manifest.json"
completed=1
printf 'Verified candidate directory: %s\n' "$output"
