# OBSİ OS User Guide

## First start

The computer opens directly into **Çalışma Alanları**. Before starting the
first VM, open **Ayarlar**:

1. Select the physical keyboard used for `ALT + F12`.
2. On a dual-GPU system, select the complete dGPU IOMMU group and reboot.
3. Select USB devices that should be attached to new workspaces.

The supported production layout keeps the iGPU on OBSİ and assigns the dGPU to
one VM at a time. Without a configured dGPU, OBSİ opens the VM through a local
SPICE viewer for setup and testing.

## Add a clean template

Open **Şablonlar → İmaj İçe Aktar**. Select a clean qcow2, raw, img or vhdx
image. Give it a short system name such as `windows11-clean` and leave the
default virtual capacity at `900G` unless a different capacity is intentional.

For Windows, install QEMU Guest Agent and run the two scripts in
`guest/windows` before freezing the source image. Hibernation must be enabled,
and the Windows system partition must be the final partition if automatic
growth is desired.

## Create and switch workspaces

Select **Yeni Çalışma Alanı**, choose the template, RAM and vCPU count, then
create. This is an instant thin snapshot: unchanged blocks remain shared with
the read-only template.

Select **Başlat**. To return, press `ALT + F12`. OBSİ removes the chord from the
guest input stream, asks the guest to hibernate to disk, waits for QEMU to
release its devices and returns to the shell. A guest without a working QEMU
Guest Agent or S4 support is deliberately left running instead of being killed.

## Storage states

- **OK** below 85%: normal.
- **WARN** from 85%: remove/archive data soon.
- **CRITICAL** from 92%: stop creating data and recover capacity.
- **DENY** from 96%: OBSİ blocks new starts and allocations.

The 30 GiB emergency reserve is a recovery tool, not ordinary free space.
