#!/usr/bin/env python3
"""Native GTK4 Metro-style shell for OBSI OS."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from obsi.client import CoreError, request


def human_bytes(value: int | float) -> str:
    number = float(value)
    for unit in ("B", "KiB", "GiB", "TiB"):
        if number < 1024 or unit == "TiB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TiB"


def clear_box(box: Gtk.Box | Gtk.FlowBox) -> None:
    child = box.get_first_child()
    while child:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


class ObsiWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application) -> None:
        super().__init__(application=app, title="OBSİ OS")
        self.set_default_size(1280, 720)
        self.fullscreen()
        self.data: dict = {"machines": [], "templates": [], "storage": {}}
        self.busy = False

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child(root)
        root.append(self._sidebar())

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.set_hexpand(True)
        root.append(main)
        main.append(self._topbar())

        self.banner = Gtk.Revealer()
        self.banner.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.banner_label = Gtk.Label(xalign=0)
        self.banner_label.add_css_class("banner")
        self.banner.set_child(self.banner_label)
        main.append(self.banner)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=180)
        self.stack.set_vexpand(True)
        self.home_page, self.home_content = self._scroll_page()
        self.templates_page, self.templates_content = self._scroll_page()
        self.storage_page, self.storage_content = self._scroll_page()
        self.settings_page, self.settings_content = self._scroll_page()
        self.stack.add_named(self.home_page, "home")
        self.stack.add_named(self.templates_page, "templates")
        self.stack.add_named(self.storage_page, "storage")
        self.stack.add_named(self.settings_page, "settings")
        main.append(self.stack)

        self.connect("close-request", self._prevent_close)
        GLib.timeout_add_seconds(3, self._poll)
        self.refresh()

    def _prevent_close(self, *_args: object) -> bool:
        return True

    def _sidebar(self) -> Gtk.Box:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.add_css_class("sidebar")
        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        logo_path = os.environ.get("OBSI_LOGO", "/usr/share/obsi/obsi-logo.svg")
        logo = Gtk.Image.new_from_file(logo_path)
        logo.set_pixel_size(34)
        brand.append(logo)
        name = Gtk.Label(label="OBSİ", xalign=0)
        name.add_css_class("brand")
        brand.append(name)
        side.append(brand)

        for icon, label, page in (
            ("view-grid-symbolic", "Çalışma Alanları", "home"),
            ("drive-harddisk-symbolic", "Şablonlar", "templates"),
            ("folder-symbolic", "Depolama", "storage"),
            ("emblem-system-symbolic", "Ayarlar", "settings"),
        ):
            button = Gtk.Button()
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.append(Gtk.Image.new_from_icon_name(icon))
            row.append(Gtk.Label(label=label, xalign=0))
            button.set_child(row)
            button.add_css_class("nav")
            button.connect("clicked", lambda _button, target=page: self.stack.set_visible_child_name(target))
            side.append(button)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        side.append(spacer)
        hint = Gtk.Label(label="ALT + F12\nÇalışma alanını kaydet ve dön", xalign=0)
        hint.add_css_class("hint")
        side.append(hint)
        return side

    def _topbar(self) -> Gtk.Box:
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        bar.add_css_class("topbar")
        title = Gtk.Label(label="Kişisel Workstation Hypervisor", xalign=0)
        title.add_css_class("top-title")
        title.set_hexpand(True)
        bar.append(title)
        self.connection = Gtk.Label(label="Bağlanıyor…")
        self.connection.add_css_class("status-pill")
        bar.append(self.connection)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Yenile")
        refresh.connect("clicked", lambda _button: self.refresh())
        bar.append(refresh)
        return bar

    def _scroll_page(self) -> tuple[Gtk.Box, Gtk.Box]:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        content.add_css_class("page")
        scroll.set_child(content)
        outer.append(scroll)
        return outer, content

    @staticmethod
    def _heading(title: str, subtitle: str) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        label = Gtk.Label(label=title, xalign=0)
        label.add_css_class("page-title")
        box.append(label)
        sub = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        sub.add_css_class("subtitle")
        box.append(sub)
        return box

    def _async(self, action: str, payload: dict | None, done=None) -> None:
        if self.busy:
            return
        self.busy = True
        self.connection.set_text("İşleniyor…")

        def worker() -> None:
            try:
                result = request(action, payload)
                GLib.idle_add(self._async_done, None, result, done)
            except (CoreError, OSError) as exc:
                GLib.idle_add(self._async_done, str(exc), None, done)

        threading.Thread(target=worker, daemon=True).start()

    def _async_done(self, error: str | None, result: dict | None, done) -> bool:
        self.busy = False
        if error:
            self.connection.set_text("Bağlantı hatası")
            self.show_message(error, error=True)
        else:
            self.connection.set_text("Sistem hazır")
            if done:
                done(result or {})
        return GLib.SOURCE_REMOVE

    def show_message(self, message: str, *, error: bool = False) -> None:
        self.banner_label.set_text(message)
        self.banner_label.remove_css_class("error")
        if error:
            self.banner_label.add_css_class("error")
        self.banner.set_reveal_child(True)
        GLib.timeout_add_seconds(5, lambda: self.banner.set_reveal_child(False) or GLib.SOURCE_REMOVE)

    def _poll(self) -> bool:
        if not self.busy:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self) -> None:
        self._async("overview", None, self._render)

    def _render(self, data: dict) -> None:
        self.data = data
        self._render_home()
        self._render_templates()
        self._render_storage()
        self._render_settings()

    def _render_home(self) -> None:
        content = self.home_content
        clear_box(content)
        content.append(self._heading("Çalışma Alanların", "Gaming, coding ve Linux ortamların tek dokunuşla hazır."))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=18, row_spacing=18)
        flow.set_max_children_per_line(3)
        flow.set_min_children_per_line(1)
        for machine in self.data.get("machines", []):
            flow.append(self._machine_card(machine))
        create = Gtk.Button()
        create.add_css_class("create-card")
        create.set_size_request(300, 190)
        create.set_child(Gtk.Label(label="＋\nYeni Çalışma Alanı"))
        create.connect("clicked", lambda _button: self._create_dialog())
        flow.append(create)
        content.append(flow)

    def _machine_card(self, machine: dict) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.add_css_class("machine-card")
        card.set_size_request(300, 190)
        icon = "🎮" if "gam" in machine["name"] else "💻"
        title = Gtk.Label(label=f"{icon}  {machine.get('title', machine['name'])}", xalign=0)
        title.add_css_class("card-title")
        card.append(title)
        state = str(machine.get("state", "tanımsız"))
        state_label = Gtk.Label(label=self._state_label(state), xalign=0)
        state_label.add_css_class("machine-state")
        card.append(state_label)
        detail = Gtk.Label(label=f"{machine.get('vcpus', 4)} vCPU  •  {int(machine.get('memory_mib', 8192)) // 1024} GB RAM", xalign=0)
        detail.add_css_class("muted")
        card.append(detail)
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        card.append(spacer)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        active = self.data.get("active") == machine["name"]
        if active and state in ("running", "blocked"):
            button = Gtk.Button(label="Kaydet ve OBSİ'ye dön")
            button.add_css_class("warning")
            button.connect("clicked", lambda _b, name=machine["name"]: self._hibernate(name))
        else:
            button = Gtk.Button(label="Devam Et" if "suspend" in state else "Başlat")
            button.add_css_class("suggested-action")
            button.connect("clicked", lambda _b, name=machine["name"]: self._start(name))
        button.set_hexpand(True)
        actions.append(button)
        menu = Gtk.MenuButton(icon_name="view-more-symbolic")
        pop = Gtk.Popover()
        pop_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        edit = Gtk.Button(label="CPU / RAM Ayarla")
        edit.connect("clicked", lambda _b, item=machine: self._edit_dialog(item))
        pop_actions.append(edit)
        delete = Gtk.Button(label="Makineyi Sil")
        delete.add_css_class("destructive-action")
        delete.connect("clicked", lambda _b, name=machine["name"]: self._delete_dialog(name))
        pop_actions.append(delete)
        pop.set_child(pop_actions)
        menu.set_popover(pop)
        actions.append(menu)
        card.append(actions)
        return card

    @staticmethod
    def _state_label(state: str) -> str:
        if state in ("running", "blocked"):
            return "● Çalışıyor"
        if "suspend" in state:
            return "● Kaydedildi"
        if state == "shut off":
            return "○ Kapalı"
        return f"○ {state}"

    def _start(self, name: str) -> None:
        self._async("machine.start", {"name": name}, lambda result: self._started(result))

    def _started(self, result: dict) -> None:
        if not result.get("gpu"):
            subprocess.Popen(["virt-viewer", "--connect", "qemu:///system", "--attach", result["name"]], start_new_session=True)
        self.show_message("Çalışma alanı başlatıldı. ALT + F12 ile OBSİ'ye dönebilirsin.")
        self.refresh()

    def _hibernate(self, name: str) -> None:
        self.show_message("Çalışma alanı diske kaydediliyor…")
        self._async("machine.hibernate", {"name": name}, lambda _result: self.refresh())

    def _create_dialog(self) -> None:
        templates = self.data.get("templates", [])
        if not templates:
            self.stack.set_visible_child_name("templates")
            self.show_message("Önce bir Windows veya Linux şablonu içe aktar.", error=True)
            return
        dialog = Gtk.Dialog(title="Yeni Çalışma Alanı", transient_for=self, modal=True)
        dialog.add_button("Vazgeç", Gtk.ResponseType.CANCEL)
        dialog.add_button("Oluştur", Gtk.ResponseType.OK)
        form = Gtk.Grid(column_spacing=14, row_spacing=14, margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
        name = Gtk.Entry(placeholder_text="gaming")
        title = Gtk.Entry(placeholder_text="Gaming")
        template = Gtk.ComboBoxText()
        for item in templates:
            template.append(item["id"], item["id"])
        template.set_active(0)
        limits = self.data.get("limits", {"memory_mib": 8192, "vcpus": 4})
        memory = Gtk.SpinButton.new_with_range(1, max(1, int(limits["memory_mib"]) // 1024), 1)
        memory.set_value(min(16, max(1, int(limits["memory_mib"]) // 1024)))
        cpu = Gtk.SpinButton.new_with_range(1, max(1, int(limits["vcpus"])), 1)
        cpu.set_value(min(8, max(1, int(limits["vcpus"]))))
        for row, (label, widget) in enumerate((("Sistem adı", name), ("Görünen ad", title), ("Şablon", template), ("RAM (GB)", memory), ("vCPU", cpu))):
            form.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            form.attach(widget, 1, row, 1, 1)
        dialog.get_content_area().append(form)

        def response(_dialog: Gtk.Dialog, result: int) -> None:
            if result == Gtk.ResponseType.OK:
                payload = {"name": name.get_text(), "title": title.get_text(), "template": template.get_active_id(), "memory_mib": memory.get_value_as_int() * 1024, "vcpus": cpu.get_value_as_int()}
                dialog.close()
                self._async("machine.create", payload, lambda _data: self.refresh())
            else:
                dialog.close()
        dialog.connect("response", response)
        dialog.present()

    def _edit_dialog(self, machine: dict) -> None:
        dialog = Gtk.Dialog(title="Çalışma Alanı Ayarları", transient_for=self, modal=True)
        dialog.add_button("Vazgeç", Gtk.ResponseType.CANCEL)
        dialog.add_button("Kaydet", Gtk.ResponseType.OK)
        form = Gtk.Grid(column_spacing=14, row_spacing=14, margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
        title = Gtk.Entry(text=str(machine.get("title", machine["name"])))
        limits = self.data.get("limits", {"memory_mib": 8192, "vcpus": 4})
        memory = Gtk.SpinButton.new_with_range(1, max(1, int(limits["memory_mib"]) // 1024), 1)
        memory.set_value(int(machine.get("memory_mib", 8192)) // 1024)
        cpu = Gtk.SpinButton.new_with_range(1, max(1, int(limits["vcpus"])), 1)
        cpu.set_value(int(machine.get("vcpus", 4)))
        for row, (label, widget) in enumerate((("Görünen ad", title), ("RAM (GB)", memory), ("vCPU", cpu))):
            form.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            form.attach(widget, 1, row, 1, 1)
        dialog.get_content_area().append(form)

        def response(_dialog: Gtk.Dialog, result: int) -> None:
            if result == Gtk.ResponseType.OK:
                payload = {"name": machine["name"], "title": title.get_text(), "memory_mib": memory.get_value_as_int() * 1024, "vcpus": cpu.get_value_as_int()}
                dialog.close()
                self._async("machine.update", payload, lambda _data: self.refresh())
            else:
                dialog.close()
        dialog.connect("response", response)
        dialog.present()

    def _delete_dialog(self, name: str) -> None:
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, buttons=Gtk.ButtonsType.NONE, message_type=Gtk.MessageType.WARNING, text="Çalışma alanı kalıcı olarak silinsin mi?", secondary_text="Bu makinenin değişiklik katmanı geri alınamaz.")
        dialog.add_button("Vazgeç", Gtk.ResponseType.CANCEL)
        dialog.add_button("Kalıcı Olarak Sil", Gtk.ResponseType.OK)
        dialog.connect("response", lambda d, r: (d.close(), self._async("machine.delete", {"name": name}, lambda _x: self.refresh())) if r == Gtk.ResponseType.OK else d.close())
        dialog.present()

    def _render_templates(self) -> None:
        content = self.templates_content
        clear_box(content)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        heading = self._heading("Şablonlar", "Temiz ana disk yalnızca okunur; her çalışma alanı sadece değişen blokları saklar.")
        heading.set_hexpand(True)
        header.append(heading)
        add = Gtk.Button(label="İmaj İçe Aktar")
        add.add_css_class("suggested-action")
        add.connect("clicked", lambda _button: self._import_dialog())
        header.append(add)
        content.append(header)
        for item in self.data.get("templates", []):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
            row.add_css_class("list-card")
            icon = Gtk.Image.new_from_icon_name("media-optical-symbolic")
            icon.set_pixel_size(32)
            row.append(icon)
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            labels.set_hexpand(True)
            label = Gtk.Label(label=item["id"], xalign=0)
            label.add_css_class("card-title")
            labels.append(label)
            labels.append(Gtk.Label(label=f"{human_bytes(item['virtual_bytes'])} sanal kapasite • %{item['mapped_percent']:.1f} fiziksel eşlenmiş", xalign=0))
            row.append(labels)
            content.append(row)

    def _import_dialog(self) -> None:
        chooser = Gtk.FileChooserNative(title="Disk İmajı Seç", transient_for=self, action=Gtk.FileChooserAction.OPEN, accept_label="Seç", cancel_label="Vazgeç")
        image_filter = Gtk.FileFilter()
        image_filter.set_name("Disk imajları")
        for pattern in ("*.qcow2", "*.raw", "*.img", "*.vhdx"):
            image_filter.add_pattern(pattern)
        chooser.add_filter(image_filter)

        def chosen(native: Gtk.FileChooserNative, response: int) -> None:
            if response != Gtk.ResponseType.ACCEPT:
                return
            selected = native.get_file()
            path = selected.get_path() if selected else None
            if not path:
                self.show_message("Yalnızca yerel dosyalar içe aktarılabilir.", error=True)
                return
            name_dialog = Gtk.Dialog(title="Şablon Bilgisi", transient_for=self, modal=True)
            name_dialog.add_button("Vazgeç", Gtk.ResponseType.CANCEL)
            name_dialog.add_button("İçe Aktar", Gtk.ResponseType.OK)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
            box.append(Gtk.Label(label="Şablon adı", xalign=0))
            entry = Gtk.Entry(placeholder_text="windows11-clean")
            box.append(entry)
            box.append(Gtk.Label(label="Sanal kapasite", xalign=0))
            size = Gtk.Entry(text="900G")
            box.append(size)
            name_dialog.get_content_area().append(box)
            name_dialog.connect("response", lambda d, r: (d.close(), self._async("template.import", {"name": entry.get_text(), "source": path, "virtual_size": size.get_text()}, lambda _x: self.refresh())) if r == Gtk.ResponseType.OK else d.close())
            name_dialog.present()
        chooser.connect("response", chosen)
        chooser.show()

    def _render_storage(self) -> None:
        content = self.storage_content
        clear_box(content)
        content.append(self._heading("Zehir Storage", "İki diskin birleşik dm-thin havuzu; sanal kapasite blok yazılana kadar fiziksel yer tüketmez."))
        storage = self.data.get("storage", {})
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        card.add_css_class("storage-card")
        level = str(storage.get("level", "unknown")).upper()
        title = Gtk.Label(label=f"OBSİ Pool  •  {level}", xalign=0)
        title.add_css_class("card-title")
        card.append(title)
        used = float(storage.get("used_bytes", 0))
        total = float(storage.get("size_bytes", 1)) or 1
        progress = Gtk.ProgressBar(fraction=min(1, used / total), show_text=True, text=f"{human_bytes(used)} kullanılıyor  /  {human_bytes(total)}")
        card.append(progress)
        meta = Gtk.Label(label=f"Veri %{float(storage.get('data_percent', 0)):.1f}  •  Metadata %{float(storage.get('metadata_percent', 0)):.1f}  •  Boş {human_bytes(storage.get('free_bytes', 0))}", xalign=0)
        card.append(meta)
        reason = Gtk.Label(label=str(storage.get("reason", "Durum bekleniyor")), xalign=0, wrap=True)
        reason.add_css_class("muted")
        card.append(reason)
        content.append(card)

    def _render_settings(self) -> None:
        content = self.settings_content
        clear_box(content)
        content.append(self._heading("Sistem Ayarları", "OBSİ host, GPU modu ve güvenli güç denetimleri."))
        gpu = self.data.get("gpu", {})
        mode = "Dedicated GPU hazır" if gpu.get("configured") else "Ekranlı sanal makine modu"
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("list-card")
        title = Gtk.Label(label=f"GPU • {mode}", xalign=0)
        title.add_css_class("card-title")
        card.append(title)
        card.append(Gtk.Label(label=", ".join(gpu.get("devices", [])) or "GPU passthrough henüz yapılandırılmadı; SPICE görüntüsü kullanılacak.", xalign=0, wrap=True))
        inventory = self.data.get("gpu_inventory", {}).get("groups", [])
        if inventory:
            gpu_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            gpu_combo = Gtk.ComboBoxText()
            for index, group in enumerate(inventory):
                gpu_combo.append(str(index), f"{group['label']} • {len(group['devices'])} aygıt")
            gpu_combo.set_active(0)
            gpu_combo.set_hexpand(True)
            gpu_row.append(gpu_combo)
            configure = Gtk.Button(label="GPU Profilini Güncelle" if gpu.get("configured") else "Dedicated GPU Yapılandır")
            configure.add_css_class("suggested-action")
            configure.connect("clicked", lambda _b: self._configure_gpu(gpu_combo, inventory))
            gpu_row.append(configure)
            card.append(gpu_row)
        content.append(card)

        input_data = self.data.get("input", {})
        input_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        input_card.add_css_class("list-card")
        input_title = Gtk.Label(label="ALT + F12 Klavyesi", xalign=0)
        input_title.add_css_class("card-title")
        input_card.append(input_title)
        input_card.append(Gtk.Label(label=input_data.get("current") or "Henüz fiziksel klavye seçilmedi.", xalign=0, wrap=True))
        keyboards = input_data.get("devices", [])
        if keyboards:
            input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            input_combo = Gtk.ComboBoxText()
            for index, keyboard in enumerate(keyboards):
                input_combo.append(str(index), keyboard["label"])
            input_combo.set_active(0)
            input_combo.set_hexpand(True)
            input_row.append(input_combo)
            choose = Gtk.Button(label="Bu Klavyeyi Kullan")
            choose.connect("clicked", lambda _b: self._configure_input(input_combo, keyboards))
            input_row.append(choose)
            input_card.append(input_row)
        else:
            input_card.append(Gtk.Label(label="Uyumlu USB klavye bulunamadı.", xalign=0))
        content.append(input_card)

        usb = self.data.get("usb", {})
        usb_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        usb_card.add_css_class("list-card")
        usb_title = Gtk.Label(label="Otomatik USB Yönlendirme", xalign=0)
        usb_title.add_css_class("card-title")
        usb_card.append(usb_title)
        usb_card.append(Gtk.Label(label="Seçilen aygıtlar yeni oluşturulan VM'lere otomatik eklenir. OBSİ klavyesini burada seçme.", xalign=0, wrap=True))
        selected = set(usb.get("selected", []))
        checks: list[tuple[Gtk.CheckButton, str]] = []
        for device in usb.get("devices", []):
            check = Gtk.CheckButton(label=f"{device['label']}  ({device['id']})")
            check.set_active(device["id"] in selected)
            checks.append((check, device["id"]))
            usb_card.append(check)
        if checks:
            apply_usb = Gtk.Button(label="USB Seçimini Uygula")
            apply_usb.connect("clicked", lambda _b: self._configure_usb(checks))
            usb_card.append(apply_usb)
        else:
            usb_card.append(Gtk.Label(label="Yönlendirilebilir USB aygıtı bulunamadı.", xalign=0))
        content.append(usb_card)

        network = self.data.get("network", {})
        network_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        network_card.add_css_class("list-card")
        network_title = Gtk.Label(label="Ağ Bağlantısı", xalign=0)
        network_title.add_css_class("card-title")
        network_card.append(network_title)
        connections = [item.get("connection") or item.get("device") for item in network.get("devices", []) if item.get("state") in ("connected", "bağlı")]
        network_card.append(Gtk.Label(label="Bağlı: " + ", ".join(connections) if connections else "Çevrimdışı", xalign=0))
        wifi = Gtk.Button(label="Wi-Fi Ağlarını Göster")
        wifi.connect("clicked", lambda _b: self._async("network.scan", {}, self._wifi_dialog))
        network_card.append(wifi)
        content.append(network_card)
        power = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        diagnostics = Gtk.Button(label="Tanılama Günlüğü")
        reboot = Gtk.Button(label="Yeniden Başlat")
        shutdown = Gtk.Button(label="Bilgisayarı Kapat")
        shutdown.add_css_class("destructive-action")
        diagnostics.connect("clicked", lambda _b: self._async("diagnostics", {}, self._diagnostics_dialog))
        reboot.connect("clicked", lambda _b: self._power_dialog("reboot"))
        shutdown.connect("clicked", lambda _b: self._power_dialog("poweroff"))
        power.append(diagnostics)
        power.append(reboot)
        power.append(shutdown)
        content.append(power)

    def _diagnostics_dialog(self, result: dict) -> None:
        dialog = Gtk.Dialog(title="OBSİ Tanılama Günlüğü", transient_for=self, modal=True)
        dialog.add_button("Kapat", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(900, 560)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, wrap_mode=Gtk.WrapMode.NONE)
        view.get_buffer().set_text(result.get("log", "Günlük kaydı yok."))
        scroll.set_child(view)
        dialog.get_content_area().append(scroll)
        dialog.connect("response", lambda d, _r: d.close())
        dialog.present()

    def _configure_input(self, combo: Gtk.ComboBoxText, keyboards: list[dict]) -> None:
        index = combo.get_active()
        if index >= 0:
            self._async("input.configure", {"path": keyboards[index]["path"]}, lambda _result: self.refresh())

    def _configure_gpu(self, combo: Gtk.ComboBoxText, groups: list[dict]) -> None:
        index = combo.get_active()
        if index < 0:
            return
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            buttons=Gtk.ButtonsType.YES_NO,
            message_type=Gtk.MessageType.WARNING,
            text="Bu IOMMU grubu sanal makinelere ayrılsın mı?",
            secondary_text="OBSİ arayüzü için başka bir iGPU/GPU gerekir. İşlem initramfs'i günceller ve yeniden başlatma ister.",
        )
        dialog.connect(
            "response",
            lambda d, r: (
                d.close(),
                self._async(
                    "gpu.configure",
                    {"devices": groups[index]["devices"]},
                    lambda _result: self.show_message("GPU hazırlandı. Ayarlar bölümünden bilgisayarı yeniden başlat."),
                ),
            )
            if r == Gtk.ResponseType.YES
            else d.close(),
        )
        dialog.present()

    def _configure_usb(self, checks: list[tuple[Gtk.CheckButton, str]]) -> None:
        selected = [identifier for check, identifier in checks if check.get_active()]
        self._async("usb.configure", {"devices": selected}, lambda _result: self.show_message("USB yönlendirme profili kaydedildi."))

    def _wifi_dialog(self, result: dict) -> None:
        networks = result.get("networks", [])
        if not networks:
            self.show_message("Görünür Wi-Fi ağı bulunamadı.", error=True)
            return
        dialog = Gtk.Dialog(title="Wi-Fi'ye Bağlan", transient_for=self, modal=True)
        dialog.add_button("Vazgeç", Gtk.ResponseType.CANCEL)
        dialog.add_button("Bağlan", Gtk.ResponseType.OK)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        combo = Gtk.ComboBoxText()
        for index, network in enumerate(networks):
            security = network.get("security") or "Açık"
            combo.append(str(index), f"{network['ssid']}  •  %{network['signal']}  •  {security}")
        combo.set_active(0)
        password = Gtk.PasswordEntry(placeholder_text="Wi-Fi parolası (açık ağda boş bırak)", show_peek_icon=True)
        box.append(combo)
        box.append(password)
        dialog.get_content_area().append(box)

        def response(_dialog: Gtk.Dialog, response_id: int) -> None:
            if response_id == Gtk.ResponseType.OK and combo.get_active() >= 0:
                chosen = networks[combo.get_active()]
                payload = {"ssid": chosen["ssid"], "password": password.get_text()}
                dialog.close()
                self._async("network.connect", payload, lambda _data: self.refresh())
            else:
                dialog.close()
        dialog.connect("response", response)
        dialog.present()

    def _power_dialog(self, operation: str) -> None:
        text = "Bilgisayar yeniden başlatılsın mı?" if operation == "reboot" else "Bilgisayar kapatılsın mı?"
        dialog = Gtk.MessageDialog(transient_for=self, modal=True, buttons=Gtk.ButtonsType.YES_NO, message_type=Gtk.MessageType.QUESTION, text=text)
        dialog.connect("response", lambda d, r: (d.close(), self._async("power", {"operation": operation, "confirm": "OBSI"})) if r == Gtk.ResponseType.YES else d.close())
        dialog.present()


class ObsiApplication(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="com.obsios.Shell")

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        css = Gtk.CssProvider()
        css_path = Path(os.environ.get("OBSI_CSS", "/usr/share/obsi/obsi.css"))
        css.load_from_path(str(css_path))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        window = self.props.active_window or ObsiWindow(self)
        window.present()


def main() -> int:
    return ObsiApplication().run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
