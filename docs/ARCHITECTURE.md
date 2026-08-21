# OBSİ OS V1 Architecture

## Runtime flow

```text
Power on
  -> UEFI + Debian + systemd
  -> obsi-storaged monitors data and metadata
  -> greetd + Cage -> unprivileged native OBSİ Shell on iGPU
  -> shell talks newline-JSON to root obsi-core over /run/obsi/core.sock
  -> choose Gaming
  -> deny if data or metadata >= 96%
  -> activate /dev/obsi-vg/vm-gaming
  -> libvirt attaches dGPU, USB and VirtIO input
  -> Windows resumes/boots
  -> Alt+F12 is intercepted by obsi-inputd
  -> QEMU Guest Agent requests guest S4
  -> GPU/input are released
  -> OBSİ Shell
```

## 1. Physical layout

```text
/dev/nvme0n1
  p1  1 GiB       EFI
  p2 63 GiB       OBSI_ROOT
  p3 remainder    LVM PV A

/dev/nvme1n1
  p1 remainder    LVM PV B

PV A + PV B -> obsi-vg
```

V1 pool construction:

```bash
lvcreate -n obsi-emergency -L 30G obsi-vg

# Installer calculates POOL_DATA as all VG free space minus 10 GiB.
lvcreate --type thin-pool \
  --name obsi-thinpool \
  --size "${POOL_DATA}B" \
  --poolmetadatasize 5G \
  --poolmetadataspare y \
  --chunksize 256K \
  --errorwhenfull y \
  obsi-vg

lvchange --monitor y obsi-vg/obsi-thinpool
```

LVM creates a metadata repair spare matching the 5 GiB active metadata LV, so
metadata plus spare consumes approximately 10 GiB. `obsi-emergency` is a real,
inactive 30 GiB LV: those extents cannot be consumed accidentally. It can be
removed and added to the pool only by the explicit emergency command.

The pool is linear capacity aggregation. It is not redundant; losing either PV
can lose data and metadata across the VG.

## 2. No boot-time disk resizing

Each V1 template receives its final virtual capacity once:

```bash
lvcreate --type thin \
  --name tpl-windows11-clean \
  --virtualsize 900G \
  --thinpool obsi-thinpool \
  --addtag obsi.template \
  obsi-vg
```

The guest sees a 900 GiB raw block device. The thin pool allocates physical
chunks only as the guest writes. Gaming, Coding, and Linux can each see 900 GiB
even when the physical pool is smaller than their combined 2.7 TiB virtual
capacity.

TRIM/discard remains important:

```xml
<disk type='block' device='disk'>
  <driver name='qemu' type='raw' cache='none' io='native' discard='unmap'/>
  <source dev='/dev/obsi-vg/vm-gaming'/>
  <target dev='vda' bus='virtio'/>
</disk>
```

The Windows/Linux guest must periodically issue TRIM so deleted guest blocks can
be unmapped from the thin pool.

## 3. Templates and workspaces

Template import writes the source image into a new thin LV with `qemu-img
convert -n -O raw`. For GPT sources, `sgdisk --move-second-header` relocates the
backup GPT from the old image boundary to the new 900 GiB boundary. OBSİ then
deactivates the template and changes it to read-only. The Windows template must
also leave its system partition last on disk; a trailing recovery partition
prevents safe online growth and is never deleted automatically by OBSİ.
Before import, the non-zero guest-visible ranges reported by `qemu-img map` plus
a 10% margin must fit below the 96% data boundary; otherwise the import is
rejected before a template LV is created.

Cloning is a native thin snapshot:

```bash
lvcreate --snapshot \
  --name vm-gaming \
  --setactivationskip n \
  --addtag obsi.machine \
  obsi-vg/tpl-windows11-clean

lvchange --permission rw obsi-vg/vm-gaming
```

No virtual-size option is supplied to the snapshot command; supplying a size
would select the wrong classic COW snapshot behavior. Template and child share
unchanged blocks and diverge only on writes.
The VM LV remains inactive until libvirt's `prepare/begin` hook passes the pool
policy and activates it.

## 4. Storage policy

`obsi-storaged` reads both `data_percent` and `metadata_percent` from LVM every
two seconds and evaluates the higher value. It also fails closed when LVM's
`lv_attr` reports `needs_check`, partial, failed, or out-of-data state:

| Usage | State | V1 action |
|---:|---|---|
| `<85%` | OK | normal operation |
| `85–91.99%` | WARN | shell warning |
| `92–95.99%` | CRITICAL | persistent critical alert |
| `>=96%` | DENY | block VM starts, template creation and cloning |

The current state is atomically published to
`/run/obsi/storage-status.json` for OBSİ Shell.

The 96% gate cannot selectively reject future writes from one already-running
VM: dm-thin operates below guest identity. V1 therefore prevents new starts and
new managed allocations. A future policy may request graceful S4 from the active
workspace at a separate emergency threshold. At physical exhaustion, the pool
is configured `error_when_full`; reaching that point can still cause guest I/O
errors and possible filesystem damage, which is why 96% is treated as a hard
operational boundary.

Both metadata and data matter. Metadata exhaustion can force repair even while
data space remains.

## 5. Emergency reserve

At CRITICAL or DENY, an administrator can run:

```bash
obsi-storage use-emergency-reserve --confirm
```

This performs:

```bash
lvremove --yes obsi-vg/obsi-emergency
lvextend --extents +100%FREE obsi-vg/obsi-thinpool
```

The operation is deliberately one-way until the pool is later reduced offline
and a new emergency LV is created. OBSİ never consumes the reserve silently.

## 6. Alt+F12 and GPU modes

`obsi-inputd` exclusively owns the physical keyboard and produces a uinput
keyboard. Libvirt consumes that device through `evdev` and exposes a VirtIO
keyboard to the guest. Alt+F12 is removed from the forwarded stream; OBSİ sends:

```bash
virsh dompmsuspend ACTIVE_VM --target disk
```

This requests guest S4 through QEMU Guest Agent. It is not QEMU managed-save and
does not attempt to serialize consumer GPU state. OBSİ declares the switch
successful only after libvirt reports `shut off`; a merely suspended QEMU still
owns VFIO devices and is deliberately not released to another workspace.

Dual GPU is the supported V1 target: iGPU remains on OBSİ Shell and dGPU remains
available to VFIO. Single-GPU host-driver unbind/reset/rebind remains
experimental because reset support is hardware-specific.

## 7. libvirt boundary

The `prepare/begin` hook runs `obsi-prestart`, which validates the machine's
native thin backend, checks the 96% policy, and activates its LV. A failed policy
check aborts domain start. Hooks never call back into libvirt; hibernation is
issued by the independent input/workspace service.

## 8. Product and privilege boundary

`obsi-shell` is a GTK4 application running as the passwordless, non-login
`obsi` system account inside the Cage Wayland kiosk compositor. There is no web
server, browser, GDM, SDDM, GNOME or KDE session. If the shell exits, greetd
starts a clean session again.

The shell cannot directly modify `/etc`, LVM, libvirt, initramfs, input devices
or system power. It connects to `/run/obsi/core.sock`, owned by `root:obsi` with
mode `0660`. `obsi-core` revalidates names, numeric ranges, disk paths, IOMMU
groups, USB IDs and destructive confirmations, and never invokes a command
through a shell string.

Machine creation is transactional: create the thin snapshot, persist the
machine record, generate libvirt XML and define the domain. A failed define
removes the snapshot and record. CPU/RAM edits are accepted only while stopped;
device-profile edits require all managed VMs to be stopped or hibernated.

## 9. Graphical installer

The Debian Live ISO automatically opens `obsi-installer` in Cage. The UI lists
whole writable disks from `lsblk`, requires two distinct disks and an explicit
`SİL` acknowledgement, then streams the existing guarded installer output. The
installed host creates the `obsi` user, enables the core/storage/network/input
services, sets `graphical.target`, disables tty1 getty, and starts the Metro
shell without a password prompt.

## Source basis

- [Linux dm-thin documentation](https://www.kernel.org/doc/html/latest/admin-guide/device-mapper/thin-provisioning.html)
- [LVM thin provisioning manual](https://man7.org/linux/man-pages/man7/lvmthin.7.html)
- [LVM lvcreate manual](https://man7.org/linux/man-pages/man8/lvcreate.8.html)
- [QEMU Guest Agent protocol](https://www.qemu.org/docs/master/interop/qemu-ga-ref.html)
- [libvirt hook lifecycle](https://www.libvirt.org/hooks.html)
- [libvirt input XML](https://libvirt.org/formatdomain.html)
- [Linux VFIO documentation](https://cdn.kernel.org/doc/html/latest/driver-api/vfio.html)
