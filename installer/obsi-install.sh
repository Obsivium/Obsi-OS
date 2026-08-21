#!/usr/bin/env bash
set -Eeuo pipefail
export LC_ALL=C

PROGRAM=${0##*/}
SYSTEM_DISK=""
POOL_DISK=""
APPLY=0
ACK=0
HOST_END_MIB=65537
VG_NAME=obsi-vg
THIN_POOL=obsi-thinpool
EMERGENCY_LV=obsi-emergency
EMERGENCY_GIB=30
THIN_METADATA_GIB=5
TARGET=/mnt/obsi-target
CLEANUP_ENABLED=0

cleanup_mounts() {
  (( CLEANUP_ENABLED )) || return 0
  local path
  for path in "$TARGET/run" "$TARGET/sys" "$TARGET/proc" "$TARGET/dev" \
              "$TARGET/boot/efi" "$TARGET"; do
    mountpoint -q "$path" 2>/dev/null && umount "$path" || true
  done
}
trap cleanup_mounts EXIT

usage() {
  cat <<'EOF'
Usage:
  obsi-install.sh --system-disk /dev/nvme0n1 --pool-disk /dev/nvme1n1
  obsi-install.sh ... --apply --yes-i-understand-data-will-be-erased

Default mode prints and validates the plan. Apply mode DESTROYS both selected
disks, installs a minimal Debian host, and creates the combined OBSİ storage VG.
Run only from a Debian Live environment.
EOF
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[OBSI] %s\n' "$*"; }
run() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
  (( APPLY )) && "$@"
}

while (($#)); do
  case "$1" in
    --system-disk) SYSTEM_DISK=${2:?missing system disk}; shift 2 ;;
    --pool-disk) POOL_DISK=${2:?missing pool disk}; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --yes-i-understand-data-will-be-erased) ACK=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n $SYSTEM_DISK && -n $POOL_DISK ]] || { usage; exit 2; }
[[ $EUID -eq 0 ]] || die "run as root in the live system"

SYSTEM_DISK=$(readlink -f -- "$SYSTEM_DISK")
POOL_DISK=$(readlink -f -- "$POOL_DISK")
[[ $SYSTEM_DISK != "$POOL_DISK" ]] || die "system and pool disks must differ"
[[ -b $SYSTEM_DISK ]] || die "not a block device: $SYSTEM_DISK"
[[ -b $POOL_DISK ]] || die "not a block device: $POOL_DISK"

part() {
  local disk=$1 number=$2
  case "$disk" in
    *[0-9]) printf '%sp%s' "$disk" "$number" ;;
    *) printf '%s%s' "$disk" "$number" ;;
  esac
}

assert_whole_disk() {
  local disk=$1 type
  type=$(lsblk -dnro TYPE -- "$disk")
  [[ $type == disk ]] || die "$disk is not a whole disk"
}

assert_not_in_use() {
  local disk=$1 mounts holders
  mounts=$(lsblk -nrpo MOUNTPOINT -- "$disk" | sed '/^$/d' || true)
  [[ -z $mounts ]] || die "$disk has mounted filesystems: $mounts"
  holders=$(lsblk -nrpo NAME,TYPE -- "$disk" | awk '$2 ~ /crypt|lvm|raid/ {print $1}' || true)
  [[ -z $holders ]] || die "$disk has active mapped children: $holders"
}

assert_minimum_size() {
  local disk=$1 minimum=$2 bytes
  bytes=$(blockdev --getsize64 "$disk")
  (( bytes >= minimum )) || die "$disk is too small"
}

assert_whole_disk "$SYSTEM_DISK"
assert_whole_disk "$POOL_DISK"
assert_not_in_use "$SYSTEM_DISK"
assert_not_in_use "$POOL_DISK"
assert_minimum_size "$SYSTEM_DISK" $((96 * 1024 * 1024 * 1024))
assert_minimum_size "$POOL_DISK" $((32 * 1024 * 1024 * 1024))

if (( APPLY )); then
  (( ACK )) || die "apply mode also requires --yes-i-understand-data-will-be-erased"
  for command in parted partprobe wipefs mkfs.vfat mkfs.ext4 \
                 pvcreate vgcreate lvcreate lvchange vgs debootstrap grub-install blkid; do
    command -v "$command" >/dev/null || die "missing required command: $command"
  done
fi

SYS_EFI=$(part "$SYSTEM_DISK" 1)
SYS_ROOT=$(part "$SYSTEM_DISK" 2)
SYS_POOL=$(part "$SYSTEM_DISK" 3)
POOL_PV=$(part "$POOL_DISK" 1)

cat <<EOF

OBSI INSTALL PLAN
  System disk : $SYSTEM_DISK
    EFI       : $SYS_EFI (1 GiB)
    Host root : $SYS_ROOT (63 GiB)
    Pool A    : $SYS_POOL (all remainder)
  Pool disk   : $POOL_DISK
    Pool B    : $POOL_PV (all remainder)
  Combined    : $VG_NAME/$THIN_POOL (native dm-thin)
  Metadata    : ${THIN_METADATA_GIB} GiB active + ${THIN_METADATA_GIB} GiB repair spare
  Emergency   : $EMERGENCY_GIB GiB reserved LV
  VM capacity : 900 GiB default virtual size; blocks allocated only when written

WARNING: this is capacity aggregation, not RAID. Either disk failing can lose
the entire VM pool.
EOF

(( APPLY )) || { log "plan only; no disk was changed"; exit 0; }

if findmnt -rn -R "$TARGET" 2>/dev/null | grep -q .; then
  die "$TARGET already contains mounts; refusing to reuse it"
fi
log "destroying partition metadata on the two explicitly selected disks"
run wipefs -a "$SYSTEM_DISK"
run wipefs -a "$POOL_DISK"

run parted -s "$SYSTEM_DISK" mklabel gpt
run parted -s "$SYSTEM_DISK" mkpart ESP fat32 1MiB 1025MiB
run parted -s "$SYSTEM_DISK" set 1 esp on
run parted -s "$SYSTEM_DISK" mkpart OBSI_ROOT ext4 1025MiB "${HOST_END_MIB}MiB"
run parted -s "$SYSTEM_DISK" mkpart OBSI_POOL_A "${HOST_END_MIB}MiB" 100%

run parted -s "$POOL_DISK" mklabel gpt
run parted -s "$POOL_DISK" mkpart OBSI_POOL_B 1MiB 100%
run partprobe "$SYSTEM_DISK"
run partprobe "$POOL_DISK"
run udevadm settle

run mkfs.vfat -F 32 -n OBSI_EFI "$SYS_EFI"
run mkfs.ext4 -F -L OBSI_ROOT "$SYS_ROOT"
run pvcreate -ff -y "$SYS_POOL" "$POOL_PV"
run vgcreate "$VG_NAME" "$SYS_POOL" "$POOL_PV"
run lvcreate -n "$EMERGENCY_LV" -L "${EMERGENCY_GIB}G" "$VG_NAME"
run lvchange --setautoactivation n --activate n "$VG_NAME/$EMERGENCY_LV"

vg_free_bytes=$(vgs --noheadings --units b --nosuffix -o vg_free "$VG_NAME" | awk '{printf "%.0f", $1}')
extent_bytes=$(vgs --noheadings --units b --nosuffix -o vg_extent_size "$VG_NAME" | awk '{printf "%.0f", $1}')
metadata_total_bytes=$((THIN_METADATA_GIB * 2 * 1024 * 1024 * 1024))
thin_data_bytes=$((vg_free_bytes - metadata_total_bytes))
thin_data_bytes=$((thin_data_bytes / extent_bytes * extent_bytes))
(( thin_data_bytes >= 64 * 1024 * 1024 * 1024 )) || die "not enough space for the thin pool"

run lvcreate --type thin-pool --name "$THIN_POOL" \
  --size "${thin_data_bytes}B" \
  --poolmetadatasize "${THIN_METADATA_GIB}G" \
  --poolmetadataspare y \
  --chunksize 256K \
  --errorwhenfull y \
  "$VG_NAME"

run mkdir -p "$TARGET"
CLEANUP_ENABLED=1
run mount "$SYS_ROOT" "$TARGET"
run mkdir -p "$TARGET/boot/efi" "$TARGET/var/lib/obsi"
run mount "$SYS_EFI" "$TARGET/boot/efi"

log "bootstrapping Debian trixie"
run debootstrap --arch amd64 trixie "$TARGET" https://deb.debian.org/debian

ROOT_UUID=$(blkid -s UUID -o value "$SYS_ROOT")
EFI_UUID=$(blkid -s UUID -o value "$SYS_EFI")

cat >"$TARGET/etc/fstab" <<EOF
UUID=$ROOT_UUID / ext4 defaults,noatime 0 1
UUID=$EFI_UUID /boot/efi vfat umask=0077 0 1
EOF

cat >"$TARGET/etc/hostname" <<'EOF'
obsi
EOF
cat >"$TARGET/etc/hosts" <<'EOF'
127.0.0.1 localhost
127.0.1.1 obsi
EOF

run mount --bind /dev "$TARGET/dev"
run mount --bind /proc "$TARGET/proc"
run mount --bind /sys "$TARGET/sys"
run mount --bind /run "$TARGET/run"

chroot "$TARGET" /bin/bash -Eeuo pipefail <<'CHROOT'
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y linux-image-amd64 systemd-sysv grub-efi-amd64 efibootmgr \
  qemu-system-x86 qemu-utils libvirt-daemon-system libvirt-daemon-config-network libvirt-clients ovmf swtpm \
  lvm2 thin-provisioning-tools gdisk python3 python3-evdev python3-gi gir1.2-gtk-4.0 \
  greetd cage virt-viewer fonts-inter network-manager nftables bridge-utils rsync locales tzdata \
  dbus-user-session udisks2 gvfs
sed -i 's/^# *tr_TR.UTF-8 UTF-8/tr_TR.UTF-8 UTF-8/' /etc/locale.gen
locale-gen
update-locale LANG=tr_TR.UTF-8
ln -sf /usr/share/zoneinfo/Europe/Istanbul /etc/localtime
echo Europe/Istanbul >/etc/timezone
install -d -m 0755 /etc/default/grub.d
case "$(awk -F: '/vendor_id/ {gsub(/ /, "", $2); print $2; exit}' /proc/cpuinfo)" in
  GenuineIntel) echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT intel_iommu=on iommu=pt"' >/etc/default/grub.d/obsi-iommu.cfg ;;
  AuthenticAMD) echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT amd_iommu=on iommu=pt"' >/etc/default/grub.d/obsi-iommu.cfg ;;
esac
grub-install --target=x86_64-efi --efi-directory=/boot/efi \
  --bootloader-id=OBSI --recheck
update-grub
systemctl enable libvirtd.service
systemctl enable lvm2-monitor.service
systemctl enable NetworkManager.service
CHROOT

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
run mkdir -p "$TARGET/opt/obsi"
run rsync -a --delete --exclude .git "$REPO_ROOT/" "$TARGET/opt/obsi/"
run chroot "$TARGET" /bin/bash /opt/obsi/installer/install-components.sh

sync
log "installation complete; target mounts will now be released"
