#!/usr/bin/env python3
"""Graphical two-disk OBSI OS installer for the Debian Live image."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402


def disk_inventory() -> list[dict]:
    result = subprocess.run(
        ["lsblk", "--json", "--bytes", "--nodeps", "--output", "NAME,PATH,SIZE,MODEL,SERIAL,TYPE,RM,RO"],
        text=True,
        capture_output=True,
        check=True,
    )
    rows = json.loads(result.stdout).get("blockdevices", [])
    return [row for row in rows if row.get("type") == "disk" and not row.get("ro") and int(row.get("size", 0)) >= 32 * 1024**3]


def size_label(size: int) -> str:
    return f"{size / 1000**3:.0f} GB"


class InstallerWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="OBSİ OS Kurulumu")
        self.fullscreen()
        self.set_default_size(1100, 700)
        self.disks = disk_inventory()
        self.process: subprocess.Popen[str] | None = None

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("installer")
        self.set_child(root)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        top.add_css_class("installer-top")
        brand = Gtk.Label(label="OBSİ OS", xalign=0)
        brand.add_css_class("brand")
        brand.set_hexpand(True)
        top.append(brand)
        top.append(Gtk.Label(label="Kişisel Workstation Hypervisor • 1.0"))
        root.append(top)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_vexpand(True)
        root.append(self.stack)
        self.stack.add_named(self._selection_page(), "select")
        self.stack.add_named(self._progress_page(), "progress")

    def _selection_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        page.set_size_request(760, -1)
        title = Gtk.Label(label="OBSİ'yi bu bilgisayara kur", xalign=0)
        title.add_css_class("installer-title")
        page.append(title)
        subtitle = Gtk.Label(label="İlk diskte 64 GB host alanı ayrılır; kalan alan ikinci diskle tek bir thin havuzda birleşir.", xalign=0, wrap=True)
        subtitle.add_css_class("subtitle")
        page.append(subtitle)

        self.system_disk = Gtk.ComboBoxText()
        self.pool_disk = Gtk.ComboBoxText()
        for disk in self.disks:
            label = f"{disk.get('model') or disk['name']}  •  {size_label(int(disk['size']))}  •  {disk['path']}"
            self.system_disk.append(disk["path"], label)
            self.pool_disk.append(disk["path"], label)
        if self.disks:
            self.system_disk.set_active(0)
            self.pool_disk.set_active(1 if len(self.disks) > 1 else 0)
        page.append(self._field("Sistem diski (EFI + 63 GB host + kalan havuz)", self.system_disk))
        page.append(self._field("İkinci havuz diski (tamamı silinir)", self.pool_disk))

        warning = Gtk.Label(label="⚠  Bu kapasite birleştirmedir, RAID değildir. Disklerden biri bozulursa VM havuzunun tamamı kaybolabilir.", xalign=0, wrap=True)
        warning.add_css_class("installer-warning")
        page.append(warning)
        confirm_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        confirm_box.append(Gtk.Label(label="İki diskin de tamamen silineceğini onaylamak için SİL yaz:", xalign=0))
        self.confirm = Gtk.Entry(placeholder_text="SİL")
        confirm_box.append(self.confirm)
        page.append(confirm_box)

        self.error = Gtk.Label(xalign=0, wrap=True)
        self.error.add_css_class("error-text")
        page.append(self.error)
        install_button = Gtk.Button(label="OBSİ OS'yi Kur")
        install_button.add_css_class("suggested-action")
        install_button.add_css_class("install-button")
        install_button.connect("clicked", self._install)
        page.append(install_button)
        return page

    @staticmethod
    def _field(label: str, widget: Gtk.Widget) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        text = Gtk.Label(label=label, xalign=0)
        text.add_css_class("field-label")
        box.append(text)
        box.append(widget)
        return box

    def _progress_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin_top=48, margin_bottom=48, margin_start=80, margin_end=80)
        title = Gtk.Label(label="OBSİ OS kuruluyor", xalign=0)
        title.add_css_class("installer-title")
        page.append(title)
        self.progress = Gtk.ProgressBar(show_text=True, text="Diskler hazırlanıyor…")
        self.progress.pulse()
        page.append(self.progress)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self.log.add_css_class("install-log")
        scroll.set_child(self.log)
        page.append(scroll)
        self.finish = Gtk.Button(label="Bilgisayarı Yeniden Başlat")
        self.finish.add_css_class("suggested-action")
        self.finish.set_visible(False)
        self.finish.connect("clicked", lambda _button: subprocess.run(["sudo", "-n", "systemctl", "reboot"], check=False))
        page.append(self.finish)
        return page

    def _install(self, _button: Gtk.Button) -> None:
        system = self.system_disk.get_active_id()
        pool = self.pool_disk.get_active_id()
        if len(self.disks) < 2:
            self.error.set_text("Kurulum için en az iki uygun fiziksel disk gerekli.")
            return
        if not system or not pool or system == pool:
            self.error.set_text("Sistem ve havuz diski farklı olmalı.")
            return
        if self.confirm.get_text().strip().upper() not in ("SİL", "SIL"):
            self.error.set_text("Silme onayı eksik.")
            return
        self.stack.set_visible_child_name("progress")
        self.process = subprocess.Popen(
            ["sudo", "-n", "/opt/obsi/installer/obsi-install.sh", "--system-disk", system, "--pool-disk", pool, "--apply", "--yes-i-understand-data-will-be-erased"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=self._watch_install, daemon=True).start()
        GLib.timeout_add(250, self._pulse)

    def _pulse(self) -> bool:
        if self.process and self.process.poll() is None:
            self.progress.pulse()
            return GLib.SOURCE_CONTINUE
        return GLib.SOURCE_REMOVE

    def _watch_install(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            GLib.idle_add(self._append_log, line)
        code = self.process.wait()
        GLib.idle_add(self._finished, code)

    def _append_log(self, line: str) -> bool:
        buffer = self.log.get_buffer()
        buffer.insert(buffer.get_end_iter(), line)
        mark = buffer.create_mark(None, buffer.get_end_iter(), False)
        self.log.scroll_mark_onscreen(mark)
        return GLib.SOURCE_REMOVE

    def _finished(self, code: int) -> bool:
        if code == 0:
            self.progress.set_fraction(1)
            self.progress.set_text("Kurulum tamamlandı")
            self.finish.set_visible(True)
        else:
            self.progress.set_fraction(0)
            self.progress.set_text("Kurulum durdu — ayrıntılar aşağıda")
            self._append_log(f"\nKurucu hata kodu: {code}\n")
        return GLib.SOURCE_REMOVE


class InstallerApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.obsios.Installer")

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        from gi.repository import Gdk
        css = Gtk.CssProvider()
        css.load_from_path(os.environ.get("OBSI_CSS", "/opt/obsi/config/obsi.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        window = self.props.active_window or InstallerWindow(self)
        window.present()


def main() -> int:
    return InstallerApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
