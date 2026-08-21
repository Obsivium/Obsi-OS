#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

getent group obsi >/dev/null || groupadd --system obsi
id obsi >/dev/null 2>&1 || useradd --system --gid obsi --home-dir /var/lib/obsi \
  --create-home --shell /usr/sbin/nologin obsi
for group in libvirt kvm input video render plugdev; do
  getent group "$group" >/dev/null && usermod -aG "$group" obsi
done

install -d -m 0755 /usr/lib/obsi /usr/lib/obsi/obsi /usr/share/obsi \
  /etc/obsi/machines /etc/libvirt/hooks/qemu.d /etc/greetd
install -m 0755 "$ROOT/bin/obsi-prestart" /usr/bin/obsi-prestart
install -m 0755 "$ROOT/bin/obsi-storage" /usr/bin/obsi-storage
install -m 0755 "$ROOT/bin/obsi-workspace" /usr/bin/obsi-workspace
install -m 0755 "$ROOT/bin/obsi-configure-input" /usr/bin/obsi-configure-input
install -m 0755 "$ROOT/bin/obsi-configure-vfio" /usr/bin/obsi-configure-vfio
install -m 0755 "$ROOT/bin/obsi-core" /usr/bin/obsi-core
install -m 0755 "$ROOT/bin/obsi-shell" /usr/bin/obsi-shell
install -m 0755 "$ROOT/bin/obsi-network-setup" /usr/bin/obsi-network-setup
install -m 0644 "$ROOT/src/obsi/__init__.py" /usr/lib/obsi/obsi/__init__.py
install -m 0644 "$ROOT/src/obsi/thin.py" /usr/lib/obsi/obsi/thin.py
install -m 0644 "$ROOT/src/obsi/client.py" /usr/lib/obsi/obsi/client.py
install -m 0644 "$ROOT/src/obsi/core.py" /usr/lib/obsi/obsi/core.py
install -m 0644 "$ROOT/src/obsi/domain.py" /usr/lib/obsi/obsi/domain.py
install -m 0644 "$ROOT/src/obsi/shell.py" /usr/lib/obsi/obsi/shell.py
install -m 0755 "$ROOT/src/obsi/inputd.py" /usr/lib/obsi/inputd.py
install -m 0755 "$ROOT/libvirt/hooks/qemu" /etc/libvirt/hooks/qemu
install -m 0644 "$ROOT/systemd/obsi-inputd.service" /etc/systemd/system/obsi-inputd.service
install -m 0644 "$ROOT/systemd/obsi-storaged.service" /etc/systemd/system/obsi-storaged.service
install -m 0644 "$ROOT/systemd/obsi-core.service" /etc/systemd/system/obsi-core.service
install -m 0644 "$ROOT/systemd/obsi-network.service" /etc/systemd/system/obsi-network.service
install -m 0644 "$ROOT/systemd/obsi-runtimefiles.conf" /usr/lib/tmpfiles.d/obsi.conf
install -m 0644 "$ROOT/systemd/obsi-modules.conf" /etc/modules-load.d/obsi.conf
install -m 0644 "$ROOT/udev/99-obsi-input.rules" /etc/udev/rules.d/99-obsi-input.rules
install -m 0644 "$ROOT/config/obsi.css" /usr/share/obsi/obsi.css
install -m 0644 "$ROOT/assets/obsi-logo.svg" /usr/share/obsi/obsi-logo.svg
install -m 0644 "$ROOT/config/greetd.toml" /etc/greetd/config.toml

python3 -m compileall -q /usr/lib/obsi
systemd-tmpfiles --create /usr/lib/tmpfiles.d/obsi.conf
systemctl daemon-reload
systemctl enable obsi-inputd.service
systemctl enable obsi-storaged.service
systemctl enable obsi-core.service
systemctl enable obsi-network.service
systemctl enable greetd.service
systemctl disable getty@tty1.service || true
systemctl set-default graphical.target
udevadm control --reload-rules

echo "OBSI components installed"
