#!/bin/sh -eu
set -eu
umask 022
[ "$(id -u)" = 0 ] || { echo 'Root builder required' >&2; exit 1; }
PROJECT=/project
helper="$PROJECT/image/alpine/standard/candidate.py"
[ -d /out ] && [ -f /out/source.json ] && [ ! -e /work/job ] || { echo 'Dedicated new job/output required' >&2; exit 1; }
[ "$(df -Pk /work | awk 'NR==2 {print $4}')" -ge 6291456 ] || { echo 'At least 6 GiB builder free space required' >&2; exit 1; }
mkdir -m 700 /work/job
job=/work/job
# Native build tools only. Target APKs remain signed official armhf packages.
apk add --no-cache alpine-sdk alpine-conf squashfs-tools git dosfstools parted e2fsprogs util-linux xz python3 mkinitfs
apk query --installed --format json '*' > /out/builder-packages.json
commit="$(python3 -c 'import json;print(json.load(open("/project/image/alpine/standard/inputs.json"))["aports_commit"])')"
url="$(python3 -c 'import json;print(json.load(open("/project/image/alpine/standard/inputs.json"))["aports_url"])')"
git init -q "$job/aports"
git -C "$job/aports" remote add origin "$url"
timeout 120 git -c http.lowSpeedLimit=1024 -c http.lowSpeedTime=30 -C "$job/aports" fetch --depth 1 origin "$commit"
git -C "$job/aports" checkout -q --detach FETCH_HEAD
[ "$(git -C "$job/aports" rev-parse HEAD)" = "$commit" ]
install -m 755 "$PROJECT/image/alpine/standard/profile-astrobridge.sh" "$job/aports/scripts/mkimg.astrobridge.sh"
install -m 755 "$PROJECT/image/alpine/standard/genapkovl-astrobridge.sh" "$job/aports/scripts/genapkovl-astrobridge.sh"
mkdir -p /root/.mkimage
install -m 755 "$PROJECT/image/alpine/standard/genapkovl-astrobridge.sh" /root/.mkimage/genapkovl-astrobridge.sh
# section_apkovl hashes the relative script from the current job directory.
install -m 755 "$PROJECT/image/alpine/standard/genapkovl-astrobridge.sh" "$job/genapkovl-astrobridge.sh"
# One measured compatibility patch to the pinned official helper, recorded below.
sed -i 's/ --no-chown//g' "$job/aports/scripts/mkimage.sh"
sha256sum "$job/aports/scripts/mkimage.sh" > /out/mkimage-patched.sha256
abuild-keygen -a -n
export PACKAGER_PRIVKEY="$(find /root/.abuild -maxdepth 1 -type f -name '*.rsa' | head -n 1)"
[ -n "$PACKAGER_PRIVKEY" ]
install -m 644 "${PACKAGER_PRIVKEY}.pub" /etc/apk/keys/
export ASTRO_BRIDGE_PROJECT="$PROJECT"
mkdir -p "$job/media"
cd "$job"
sh "$job/aports/scripts/mkimage.sh" --tag astro-bridge --outdir "$job/media" \
  --workdir "$job/mkimage" --profile astrobridge --arch armhf --hostkeys \
  --repository https://dl-cdn.alpinelinux.org/alpine/v3.24/main \
  --repository https://dl-cdn.alpinelinux.org/alpine/v3.24/community
set -- "$job/media/"*.tar.gz
[ "$#" = 1 ] && [ -f "$1" ]
archive="$1"
image="$job/candidate.img"
truncate -s 1536M "$image"
parted -s "$image" mklabel msdos mkpart primary fat32 1MiB 513MiB mkpart primary ext4 513MiB 100% set 1 boot on
boot_loop= data_loop=
cleanup() {
  set +e
  if [ -n "$boot_loop" ]; then
    if mountpoint -q "$job/boot"; then
      [ "$(findmnt -n -o SOURCE --target "$job/boot")" = "$boot_loop" ] && umount "$job/boot"
      if mountpoint -q "$job/boot"; then echo "Owned loop still mounted; refusing detach" >&2; return 1; fi
    fi
    python3 "$helper" guard-loop "$boot_loop" "$image" 1048576 536870912 && losetup -d "$boot_loop"
  fi
  if [ -n "$data_loop" ]; then
    if mountpoint -q "$job/data"; then
      [ "$(findmnt -n -o SOURCE --target "$job/data")" = "$data_loop" ] && umount "$job/data"
      if mountpoint -q "$job/data"; then echo "Owned DATA loop still mounted; refusing detach" >&2; return 1; fi
    fi
    python3 "$helper" guard-loop "$data_loop" "$image" 537919488 1072693248 && losetup -d "$data_loop"
  fi
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
boot_loop="$(losetup --find --show --offset 1048576 --sizelimit 536870912 "$image")"
python3 "$helper" guard-loop "$boot_loop" "$image" 1048576 536870912
mkfs.vfat -F 32 -n ALPINE "$boot_loop"
data_loop="$(losetup --find --show --offset 537919488 --sizelimit 1072693248 "$image")"
python3 "$helper" guard-loop "$data_loop" "$image" 537919488 1072693248
mkfs.ext4 -q -F -L ASTRO_DATA "$data_loop"
[ "$(blkid -s LABEL -o value "$boot_loop")" = ALPINE ]
[ "$(blkid -s LABEL -o value "$data_loop")" = ASTRO_DATA ]
# mkfs.ext4 starts its root at0755; provisioning deliberately requires0700.
# Initialize the filesystem itself, not just the mountpoint underneath it.
mkdir -p "$job/data"
mount "$data_loop" "$job/data"
python3 "$helper" init-data "$job/data" "$data_loop" "$image" 537919488 1072693248 /out/filesystems.json
sync
umount "$job/data"
fsck.fat -n "$boot_loop"
e2fsck -f -n "$data_loop"
mkdir -p "$job/boot"
mount "$boot_loop" "$job/boot"
tar -xzf "$archive" -C "$job/boot"
# Verify original package signatures and locally signed index using public keys.
mkdir -p "$job/keys" "$job/closure"
cp /etc/apk/keys/*.pub "$job/keys/"
[ ! -d /usr/share/apk/keys/armhf ] || cp /usr/share/apk/keys/armhf/*.pub "$job/keys/"
for package in "$job/boot/apks/armhf/"*.apk; do apk --keys-dir "$job/keys" verify "$package"; done
world="$(tar -xOf "$job/boot/"*.apkovl.tar.gz ./etc/apk/world)"
# Fixed generated package names, not shell code. Solver runs offline, without
# target scripts; successful resolution proves the actual boot-media closure.
# shellcheck disable=SC2086
apk --arch armhf --root "$job/closure" --keys-dir "$job/keys" --repositories-file /dev/null \
  --no-network --repository "$job/boot/apks" add --initdb --no-scripts $world
apk --root "$job/closure" query --installed --format json '*' > /out/target-packages.json
apk --root "$job/mkimage/apkroot-armhf" query --format json linux-rpi linux-firmware raspberrypi-bootloader raspberrypi-bootloader-common > /out/kernel-packages.json
apk --root "$job/closure" query --installed --format json --fields name,version,origin,license,commit '*' > /out/source-packages.json
apk --root "$job/mkimage/apkroot-armhf" query --format json --fields name,version,origin,license,commit linux-rpi linux-firmware raspberrypi-bootloader raspberrypi-bootloader-common > /out/source-kernel-packages.json
unsquashfs -ll "$job/boot/boot/modloop-rpi" > "$job/modules.txt"
python3 "$helper" inspect "$job/boot" "$job/closure" /out/target-packages.json "$job/modules.txt" "$job/keys" /out/assembled.json
# Keep immutable payload/index identities for exact version/hash pinning, not a
# promise that the upstream repository branch will retain these same versions.
python3 - "$job/boot" /out "$job/mkimage" <<'PY'
import hashlib,json,sys
from pathlib import Path
boot,out,cache=map(Path,sys.argv[1:]);files={};cached={}
for p in (boot/'apks').rglob('*'):
    if p.is_file():
        with p.open('rb') as stream:files[str(p.relative_to(boot))]=hashlib.file_digest(stream,'sha256').hexdigest()
for p in cache.rglob('*.apk'):
    with p.open('rb') as stream:cached[str(p.relative_to(cache))]=hashlib.file_digest(stream,'sha256').hexdigest()
(out/'package-lock.json').write_text(json.dumps({'architecture':'armhf','signed_apks_verified':True,'offline_solver_verified':True,'media_files':files,'kernel_cache_files':cached,'packages':json.loads((out/'target-packages.json').read_text())},indent=2)+'\n')
PY
sync
umount "$job/boot"
python3 "$helper" guard-loop "$boot_loop" "$image" 1048576 536870912
losetup -d "$boot_loop";boot_loop=
python3 "$helper" guard-loop "$data_loop" "$image" 537919488 1072693248
losetup -d "$data_loop";data_loop=
python3 "$helper" finish "$image" /out
