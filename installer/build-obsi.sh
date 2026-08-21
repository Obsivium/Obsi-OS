#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "run as root on Debian" >&2; exit 1; }
command -v lb >/dev/null || { echo "install live-build first" >&2; exit 1; }
command -v rsync >/dev/null || { echo "install rsync first" >&2; exit 1; }

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
BUILD="$ROOT/build/live"
DIST="$ROOT/dist"

mkdir -p "$BUILD/config/includes.chroot/opt/obsi" \
  "$BUILD/config/package-lists" \
  "$BUILD/config/includes.chroot/etc/greetd" \
  "$BUILD/config/includes.chroot/etc/sudoers.d" \
  "$BUILD/config/includes.chroot/etc/systemd/system/graphical.target.wants" \
  "$DIST"
rsync -a --delete --exclude .git --exclude build --exclude dist \
  "$ROOT/" "$BUILD/config/includes.chroot/opt/obsi/"
install -m 0644 "$ROOT/iso/obsi.list.chroot" \
  "$BUILD/config/package-lists/obsi.list.chroot"
install -m 0644 "$ROOT/iso/greetd-live.toml" \
  "$BUILD/config/includes.chroot/etc/greetd/config.toml"
install -m 0440 "$ROOT/iso/99-obsi-installer" \
  "$BUILD/config/includes.chroot/etc/sudoers.d/99-obsi-installer"
ln -sfn /lib/systemd/system/greetd.service \
  "$BUILD/config/includes.chroot/etc/systemd/system/graphical.target.wants/greetd.service"

cd "$BUILD"
lb config --distribution trixie --architecture amd64 --binary-image iso-hybrid \
  --bootloaders "grub-efi" --uefi-secure-boot auto \
  --archive-areas "main contrib non-free-firmware" \
  --bootappend-live "boot=live components quiet splash systemd.unit=graphical.target"
lb build
cp -f live-image-amd64.hybrid.iso "$DIST/OBSI-OS-1.0.0-amd64.iso"
sha256sum "$DIST/OBSI-OS-1.0.0-amd64.iso" >"$DIST/OBSI-OS-1.0.0-amd64.iso.sha256"
echo "Built $DIST/OBSI-OS-1.0.0-amd64.iso"
