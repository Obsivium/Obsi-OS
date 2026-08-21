# OBSİ OS V1

OBSİ OS is a local-screen-first personal workstation hypervisor. It boots into
an appliance shell, launches KVM workspaces from block-level templates, and
returns to the shell without rebooting the host.

V1 foundation now includes:

- guarded `64 GiB host + two-disk LVM VG` installation;
- native device-mapper thin provisioning;
- 900 GiB default virtual disks with allocation only on write;
- read-only template thin LVs and instant thin snapshots;
- 85% warning, 92% critical, and 96% new-start/allocation denial policy;
- 30 GiB emergency pool-extension reserve;
- dual-GPU VFIO profile generation;
- `Alt+F12 -> guest S4 hibernate -> OBSİ Shell` input routing;
- a native GTK4 Metro shell with no browser or login manager;
- graphical two-disk Live installer with explicit destructive confirmation;
- GUI template import, VM create/edit/delete/start/hibernate flows;
- GUI GPU/IOMMU, VirtIO keyboard, USB routing, storage and power settings;
- a root-only OBSİ Core API over a permission-restricted Unix socket;
- automatic greetd+Cage local session, libvirt NAT and Windows guest integration.

The shell runs as the locked-down `obsi` system user. It never executes LVM,
libvirt, VFIO or power commands directly; the privileged core validates every
request and invokes fixed argument arrays without a command shell.

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before using the destructive
installer.

## Storage model

```text
Disk 1 remainder + Disk 2
            -> obsi-vg
                 |- 5 GiB thin metadata
                 |- 5 GiB LVM metadata repair spare
                 |- 30 GiB obsi-emergency
                 `- all remaining extents: obsi-thinpool data

tpl-windows11-clean  900 GiB virtual, read-only
  |- vm-gaming       900 GiB virtual, changed blocks only
  |- vm-coding       900 GiB virtual, changed blocks only
  `- vm-linux        900 GiB virtual, changed blocks only
```

Virtual capacities are not physical reservations. A VM consumes thin-pool data
only when it writes a new block. qcow2 is accepted only as an import source; V1
VM disks are raw LVM thin block devices and do not stack qcow2 over dm-thin.

## Graphical installation

Boot `OBSI-OS-1.0.0-amd64.iso`. The machine opens directly into the installer:

1. choose the system disk and the second pool disk;
2. inspect the `1 GiB EFI + 63 GiB host + combined remainder` plan;
3. type `SİL` and start installation;
4. reboot into the OBSİ Metro shell.

The CLI below remains available for recovery and unattended laboratory use.

From Debian Live, plan mode changes nothing:

```bash
sudo bash installer/obsi-install.sh \
  --system-disk /dev/nvme0n1 \
  --pool-disk /dev/nvme1n1
```

Apply mode erases both selected disks and requires the full acknowledgement:

```bash
sudo bash installer/obsi-install.sh \
  --system-disk /dev/nvme0n1 \
  --pool-disk /dev/nvme1n1 \
  --apply \
  --yes-i-understand-data-will-be-erased
```

This is capacity aggregation, not RAID. Failure of either member disk can lose
the entire VM pool.

## Template workflow

Normal use is graphical: open **Şablonlar → İmaj İçe Aktar**, select a clean
qcow2/raw/img/vhdx image, then create Gaming/Coding cards from it. The commands
below expose the same backend for recovery.

Import an existing clean image directly into a 900 GiB thin template:

```bash
obsi-storage import-template windows11-clean ./Windows11-Clean.qcow2
obsi-storage clone windows11-clean gaming
obsi-storage clone windows11-clean coding
obsi-storage list
```

Each clone also writes `/etc/obsi/machines/MACHINE.json`, which is consumed by
the libvirt start gate.

Or create an empty install target, install the guest, then freeze it:

```bash
obsi-storage create-template windows11-clean --virtual-size 900G
# Attach /dev/obsi-vg/tpl-windows11-clean to the installer VM.
obsi-storage finalize-template windows11-clean
```

Pool state:

```bash
obsi-storage status
obsi-storage status --json
```

At CRITICAL/DENY only, the explicit recovery command consumes the 30 GiB
reserve and extends the pool:

```bash
obsi-storage use-emergency-reserve --confirm
```

## Validation

```bash
python3 -m unittest discover -s tests -v
bash -n installer/*.sh bin/obsi-workspace \
  bin/obsi-configure-input bin/obsi-configure-vfio libvirt/hooks/qemu
```

An ISO built with `sudo bash installer/build-obsi.sh` contains the graphical
installer and the full installed appliance under `/opt/obsi`.

See [the user guide](docs/USER_GUIDE.md) and the mandatory
[hardware acceptance checklist](docs/HARDWARE_ACCEPTANCE.md). Static validation
does not replace a destructive install, real VFIO reset, Windows QGA/S4, or
power-loss recovery test on the target hardware.
