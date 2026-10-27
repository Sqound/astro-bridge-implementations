#!/bin/bash
# Produce a physical-boot diagnostic image using Alpine's documented Zero W
# layout: its unmodified armhf boot archive at the root of one FAT32 volume.
# This intentionally contains no Astro Bridge customisation.
set -euo pipefail
cd "$(dirname "$0")/../.."
IMAGE=/work/alpine-zero-w-baseline.img
MOUNT=/work/alpine-zero-w-baseline-boot
LOOP=""
cleanup() {
  set +e
  mountpoint -q "$MOUNT" && umount "$MOUNT"
  [ -z "$LOOP" ] || losetup -d "$LOOP"
}
trap cleanup EXIT

rm -f "$IMAGE"
truncate -s 512M "$IMAGE"
parted -s "$IMAGE" mklabel msdos mkpart primary fat32 1MiB 100% set 1 boot on
LOOP="$(losetup --find --show --offset 1048576 "$IMAGE")"
mkfs.vfat -F 32 -n ALPINE "$LOOP"
mkdir -p "$MOUNT"
mount "$LOOP" "$MOUNT"
tar -xzf image/cache/alpine/boot.tar.gz -C "$MOUNT"

# Validate the pre-kernel files that the Zero W boot ROM must locate.
for path in bootcode.bin start.elf fixup.dat config.txt cmdline.txt boot/vmlinuz-rpi boot/initramfs-rpi; do
  test -f "$MOUNT/$path"
done
sync
cleanup
LOOP=""
trap - EXIT

mkdir -p dist
xz -T2 -3 -c "$IMAGE" > dist/alpine-zero-w-official-baseline.img.xz.partial
mv dist/alpine-zero-w-official-baseline.img.xz.partial dist/alpine-zero-w-official-baseline.img.xz
cd dist
sha256sum alpine-zero-w-official-baseline.img.xz > ALPINE-ZERO-W-OFFICIAL-BASELINE-SHA256SUMS
