# OBSİ OS Hardware Acceptance Gate

Do not label a hardware profile supported until all items pass on the actual
machine. This gate is intentionally separate from unit and ISO build checks.

## Destructive install and recovery

- Verify the graphical installer shows the exact two selected disk serials.
- Confirm the host root is 63 GiB plus 1 GiB EFI and both remainder PVs are in
  `obsi-vg`.
- Verify thin metadata, metadata spare and the inactive 30 GiB emergency LV.
- Pull power during template import on a disposable installation; boot, run LVM
  checks and confirm the pool fails closed rather than starting VMs blindly.
- Simulate 85%, 92% and 96% data and metadata thresholds.

## GPU and device lifecycle

- Confirm every selected dGPU endpoint is in one complete IOMMU group.
- Cold boot and verify the dGPU is bound to `vfio-pci` while the iGPU keeps the
  OBSİ shell visible.
- Run 20 cycles each of Gaming start → `ALT + F12` → Coding start.
- Confirm graphics, GPU audio, keyboard and selected USB devices work after
  every cycle; inspect the journal for reset or AER errors.
- Confirm an unsupported/partial IOMMU group is refused by the UI.

## Guest S4 and storage

- Install QEMU Guest Agent and OBSİ guest integration in each template.
- Resume Windows/Linux with applications open and verify state after 20 cycles.
- Confirm `ALT + F12` never reaches the guest.
- Confirm a failed S4 request leaves the VM running and shows an error.
- Write, delete and TRIM a large guest file; verify dm-thin mapped usage falls.
- Fill toward 96% and verify a new VM cannot start.

## Appliance behavior

- Confirm power-on reaches the Metro shell without a password, terminal, phone
  or web UI.
- Kill the shell and Cage processes; greetd must recover the local session.
- Disconnect/reconnect configured input and USB devices.
- Test shutdown/reboot, loss of network, corrupt machine JSON and a stopped
  `libvirtd`; the GUI must show an error without corrupting storage.
