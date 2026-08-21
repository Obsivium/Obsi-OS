#!/usr/bin/env python3
"""Privileged OBSI Core daemon.

The graphical shell is intentionally unprivileged. Every state-changing request
is validated again here and executed without a shell.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socketserver
import subprocess
import tempfile
import threading
from pathlib import Path

from obsi.domain import DomainSpec, build_domain_xml

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
TITLE_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,64}$")
PCI_RE = re.compile(r"^0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
USB_RE = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")
MAX_REQUEST = 1024 * 1024


class RequestError(RuntimeError):
    pass


def run(args: list[str], *, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=check,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise RequestError(f"işlem zaman aşımına uğradı: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RequestError(detail or f"işlem başarısız: {args[0]}") from exc


def clean_name(value: object, label: str = "makine") -> str:
    name = str(value or "").strip().lower()
    if not NAME_RE.fullmatch(name):
        raise RequestError(f"{label} adı küçük harf, rakam ve tire içermeli")
    return name


def clean_title(value: object) -> str:
    title = str(value or "").strip()
    if not TITLE_RE.fullmatch(title):
        raise RequestError("görünen ad 1-64 güvenli karakter olmalı")
    return title


def bounded_int(value: object, minimum: int, maximum: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise RequestError(f"{label} sayı olmalı") from exc
    if not minimum <= number <= maximum:
        raise RequestError(f"{label} {minimum}-{maximum} arasında olmalı")
    return number


def split_nmcli(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for character in line.rstrip("\n"):
        if escaped:
            current.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


class Core:
    def __init__(self, config_root: Path = Path("/etc/obsi"), runtime: Path = Path("/run/obsi")) -> None:
        self.config_root = config_root
        self.machine_root = config_root / "machines"
        self.runtime = runtime
        self.lock = threading.RLock()

    def _json_command(self, args: list[str]) -> object:
        result = run(args)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RequestError(f"geçersiz sistem yanıtı: {args[0]}") from exc

    @staticmethod
    def _firmware_paths() -> tuple[str, str]:
        code_candidates = (
            "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
            "/usr/share/OVMF/OVMF_CODE_4M.fd",
            "/usr/share/OVMF/OVMF_CODE.fd",
        )
        vars_candidates = (
            "/usr/share/OVMF/OVMF_VARS_4M.ms.fd",
            "/usr/share/OVMF/OVMF_VARS_4M.fd",
            "/usr/share/OVMF/OVMF_VARS.fd",
        )
        code = next((path for path in code_candidates if Path(path).exists()), None)
        variables = next((path for path in vars_candidates if Path(path).exists()), None)
        if not code or not variables:
            raise RequestError("uyumlu OVMF firmware dosyaları bulunamadı")
        return code, variables

    def _domain_state(self, name: str) -> str:
        result = run(["virsh", "domstate", name], timeout=10, check=False)
        return result.stdout.strip().lower() if result.returncode == 0 else "tanımsız"

    @staticmethod
    def resource_limits() -> dict:
        memory_mib = 2048
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    memory_mib = max(1024, int(line.split()[1]) // 1024 - 2048)
                    break
        except (OSError, ValueError, IndexError):
            pass
        return {"memory_mib": min(memory_mib, 1048576), "vcpus": max(1, os.cpu_count() or 1)}

    def _requested_resources(self, memory: object, vcpus: object) -> tuple[int, int]:
        limits = self.resource_limits()
        return (
            bounded_int(memory, 1024, limits["memory_mib"], "bellek"),
            bounded_int(vcpus, 1, limits["vcpus"], "işlemci"),
        )

    def _read_machine(self, path: Path) -> dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestError(f"bozuk makine kaydı: {path.stem}") from exc
        name = clean_name(path.stem)
        payload.update(
            {
                "name": name,
                "title": payload.get("title", name.replace("-", " ").title()),
                "state": self._domain_state(name),
            }
        )
        return payload

    def _machine_xml(self, name: str, config: dict) -> str:
        firmware, variables = self._firmware_paths()
        return build_domain_xml(
            DomainSpec(
                name=name,
                title=clean_title(config.get("title") or name.replace("-", " ").title()),
                disk=str(config["disk"]),
                memory_mib=bounded_int(config.get("memory_mib", 8192), 1024, 1048576, "bellek"),
                vcpus=bounded_int(config.get("vcpus", 4), 1, 256, "işlemci"),
                firmware=firmware,
                nvram_template=variables,
                gpu_devices=self._gpu_devices(),
                usb_devices=self._usb_devices(),
            )
        )

    def _define_machine(self, name: str, config: dict) -> None:
        xml = self._machine_xml(name, config)
        self.runtime.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=self.runtime, suffix=".xml", delete=False
        ) as stream:
            stream.write(xml)
            xml_path = Path(stream.name)
        try:
            run(["virsh", "define", str(xml_path)])
        finally:
            xml_path.unlink(missing_ok=True)

    def _redefine_stopped_machines(self) -> None:
        paths = sorted(self.machine_root.glob("*.json"))
        active = [path.stem for path in paths if self._domain_state(path.stem) not in ("shut off", "tanımsız")]
        if active:
            raise RequestError("aygıt profili değiştirilmeden önce tüm VM'leri kapat veya hibernate et")
        for path in paths:
            self._define_machine(path.stem, json.loads(path.read_text(encoding="utf-8")))

    def overview(self) -> dict:
        storage_path = self.runtime / "storage-status.json"
        try:
            storage = json.loads(storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            storage = self._json_command(["obsi-storage", "status", "--json"])
        volumes = self._json_command(["obsi-storage", "list", "--json"])
        machines = [self._read_machine(path) for path in sorted(self.machine_root.glob("*.json"))]
        templates = [item for item in volumes if item.get("kind") == "template"]
        active_path = self.runtime / "active-machine"
        try:
            active = clean_name(active_path.read_text(encoding="utf-8").strip())
        except (OSError, RequestError):
            active = None
        return {
            "storage": storage,
            "machines": machines,
            "templates": templates,
            "active": active,
            "gpu": {"configured": bool(self._gpu_devices()), "devices": list(self._gpu_devices())},
            "host": {"hostname": os.uname().nodename, "version": "1.0.0"},
            "limits": self.resource_limits(),
            "input": self.input_status(),
            "gpu_inventory": self.gpu_inventory(),
            "usb": self.usb_status(),
            "network": self.network_status(),
        }

    def _gpu_devices(self) -> tuple[str, ...]:
        path = self.config_root / "gpu.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestError("GPU ayarı okunamadı") from exc
        devices = tuple(str(item).lower() for item in payload.get("devices", []))
        if any(not PCI_RE.fullmatch(item) for item in devices):
            raise RequestError("GPU ayarında geçersiz PCI adresi var")
        return devices

    def input_status(self) -> dict:
        devices = []
        for path in sorted(Path("/dev/input/by-id").glob("*-event-kbd")):
            devices.append(
                {
                    "path": str(path),
                    "label": path.name.replace("-event-kbd", "").replace("usb-", ""),
                }
            )
        try:
            current = json.loads(
                (self.config_root / "inputd.json").read_text(encoding="utf-8")
            ).get("keyboard")
        except (OSError, json.JSONDecodeError):
            current = None
        return {"configured": bool(current), "current": current, "devices": devices}

    def configure_input(self, payload: dict) -> dict:
        selected = str(payload.get("path", ""))
        allowed = {item["path"] for item in self.input_status()["devices"]}
        if selected not in allowed:
            raise RequestError("geçersiz veya artık bağlı olmayan klavye")
        run(["obsi-configure-input", selected])
        return self.input_status()

    def gpu_inventory(self) -> dict:
        groups: list[dict] = []
        seen: set[str] = set()
        for device in sorted(Path("/sys/bus/pci/devices").glob("0000:*")):
            try:
                pci_class = (device / "class").read_text().strip().lower()
                link = (device / "iommu_group").resolve(strict=True)
            except OSError:
                continue
            if not pci_class.startswith(("0x0300", "0x0302")):
                continue
            group = link.name
            if group in seen:
                continue
            seen.add(group)
            members = []
            for member in sorted((link / "devices").iterdir()):
                try:
                    member_class = (member / "class").read_text().strip().lower()
                except OSError:
                    continue
                if member_class[2:4] != "06":
                    members.append(member.name.lower())
            groups.append(
                {
                    "group": group,
                    "gpu": device.name.lower(),
                    "devices": members,
                    "label": f"PCI {device.name.lower()} • IOMMU {group}",
                }
            )
        return {"groups": groups, "configured": list(self._gpu_devices())}

    def configure_gpu(self, payload: dict) -> dict:
        devices = [str(item).lower() for item in payload.get("devices", [])]
        inventories = [item["devices"] for item in self.gpu_inventory()["groups"]]
        if devices not in inventories:
            raise RequestError("GPU için tam ve geçerli bir IOMMU grubu seçilmeli")
        self._redefine_stopped_machines()
        run(["obsi-configure-vfio", *devices], timeout=300)
        self._redefine_stopped_machines()
        return {"devices": devices, "reboot_required": True}

    def usb_inventory(self) -> list[dict]:
        devices = []
        for path in sorted(Path("/sys/bus/usb/devices").glob("*")):
            try:
                vendor = (path / "idVendor").read_text().strip().lower()
                product = (path / "idProduct").read_text().strip().lower()
                device_class = (path / "bDeviceClass").read_text().strip().lower()
            except OSError:
                continue
            if device_class == "09":
                continue
            try:
                manufacturer = (path / "manufacturer").read_text().strip()
            except OSError:
                manufacturer = "USB"
            try:
                product_name = (path / "product").read_text().strip()
            except OSError:
                product_name = f"{vendor}:{product}"
            devices.append(
                {
                    "id": f"{vendor}:{product}",
                    "vendor": vendor,
                    "product": product,
                    "label": f"{manufacturer} {product_name}".strip(),
                }
            )
        unique = {item["id"]: item for item in devices}
        return list(unique.values())

    def _usb_devices(self) -> tuple[tuple[str, str], ...]:
        try:
            payload = json.loads((self.config_root / "usb.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestError("USB yönlendirme ayarı okunamadı") from exc
        identifiers = [str(item).lower() for item in payload.get("devices", [])]
        if any(not USB_RE.fullmatch(item) for item in identifiers):
            raise RequestError("USB ayarında geçersiz aygıt kimliği var")
        return tuple(tuple(item.split(":", 1)) for item in identifiers)  # type: ignore[return-value]

    def usb_status(self) -> dict:
        selected = [f"{vendor}:{product}" for vendor, product in self._usb_devices()]
        return {"devices": self.usb_inventory(), "selected": selected}

    def configure_usb(self, payload: dict) -> dict:
        selected = [str(item).lower() for item in payload.get("devices", [])]
        allowed = {item["id"] for item in self.usb_inventory()}
        if len(selected) > 16 or len(set(selected)) != len(selected) or any(item not in allowed for item in selected):
            raise RequestError("USB seçiminde geçersiz veya yinelenen aygıt var")
        self._redefine_stopped_machines()
        self.config_root.mkdir(parents=True, exist_ok=True)
        destination = self.config_root / "usb.json"
        temporary = destination.with_suffix(".json.new")
        temporary.write_text(json.dumps({"devices": selected}, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o640)
        os.replace(temporary, destination)
        self._redefine_stopped_machines()
        return self.usb_status()

    def network_status(self) -> dict:
        result = run(
            ["nmcli", "--terse", "--escape", "yes", "--fields", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"],
            timeout=15,
            check=False,
        )
        devices = []
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                fields = split_nmcli(line)
                if len(fields) == 4 and fields[1] in ("wifi", "ethernet"):
                    devices.append(dict(zip(("device", "type", "state", "connection"), fields)))
        return {"devices": devices, "online": any(item["state"] == "connected" for item in devices)}

    def network_scan(self, _payload: dict) -> dict:
        result = run(
            ["nmcli", "--terse", "--escape", "yes", "--fields", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"],
            timeout=30,
        )
        networks: dict[str, dict] = {}
        for line in result.stdout.splitlines():
            fields = split_nmcli(line)
            if len(fields) != 3 or not fields[0]:
                continue
            try:
                signal = int(fields[1])
            except ValueError:
                signal = 0
            candidate = {"ssid": fields[0], "signal": signal, "security": fields[2]}
            if signal > networks.get(fields[0], {}).get("signal", -1):
                networks[fields[0]] = candidate
        return {"networks": sorted(networks.values(), key=lambda item: item["signal"], reverse=True)}

    def network_connect(self, payload: dict) -> dict:
        ssid = str(payload.get("ssid", ""))
        password = str(payload.get("password", ""))
        if not ssid or len(ssid.encode("utf-8")) > 32 or any(ord(char) < 32 for char in ssid):
            raise RequestError("geçersiz Wi-Fi adı")
        if password and not 8 <= len(password) <= 63:
            raise RequestError("Wi-Fi parolası 8-63 karakter olmalı")
        args = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            args.extend(["password", password])
        run(args, timeout=60)
        return self.network_status()

    def create_machine(self, payload: dict) -> dict:
        name = clean_name(payload.get("name"))
        title = clean_title(payload.get("title") or name.replace("-", " ").title())
        template = clean_name(payload.get("template"), "şablon")
        memory, vcpus = self._requested_resources(payload.get("memory_mib", 8192), payload.get("vcpus", 4))
        config_path = self.machine_root / f"{name}.json"
        if config_path.exists() or run(["virsh", "dominfo", name], check=False).returncode == 0:
            raise RequestError("bu makine adı zaten kullanılıyor")

        cloned = False
        try:
            run(["obsi-storage", "clone", template, name])
            cloned = True
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update({"title": title, "memory_mib": memory, "vcpus": vcpus, "os": payload.get("os", "windows")})
            temporary = config_path.with_suffix(".json.new")
            temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o640)
            os.replace(temporary, config_path)

            self._define_machine(name, config)
            return {"machine": self._read_machine(config_path)}
        except Exception:
            run(["virsh", "undefine", name, "--nvram"], check=False)
            if cloned:
                run(["obsi-storage", "remove-machine", name, "--confirm"], check=False)
            raise

    def import_template(self, payload: dict) -> dict:
        name = clean_name(payload.get("name"), "şablon")
        source = Path(str(payload.get("source", ""))).resolve()
        if not source.is_file():
            raise RequestError("seçilen imaj dosyası bulunamadı")
        size = str(payload.get("virtual_size", "900G")).upper()
        run(["obsi-storage", "import-template", name, str(source), "--virtual-size", size], timeout=7200)
        return {"name": name}

    def start_machine(self, payload: dict) -> dict:
        name = clean_name(payload.get("name"))
        if not self.input_status()["configured"]:
            raise RequestError("önce Ayarlar bölümünden ALT + F12 klavyesini seç")
        if not Path("/dev/input/by-id/obsi-guest-keyboard").exists():
            raise RequestError("OBSI sanal klavyesi hazır değil; klavye ayarını yeniden uygula")
        run(["obsi-workspace", "start", name], timeout=180)
        return {"name": name, "state": self._domain_state(name), "gpu": bool(self._gpu_devices())}

    def hibernate_machine(self, payload: dict) -> dict:
        name = clean_name(payload.get("name"))
        run(["obsi-workspace", "hibernate", name], timeout=150)
        return {"name": name, "state": self._domain_state(name)}

    def delete_machine(self, payload: dict) -> dict:
        name = clean_name(payload.get("name"))
        state = self._domain_state(name)
        if state not in ("shut off", "tanımsız"):
            raise RequestError("çalışan veya uyuyan makine silinemez")
        run(["virsh", "undefine", name, "--nvram"], check=False)
        run(["obsi-storage", "remove-machine", name, "--confirm"])
        active = self.runtime / "active-machine"
        try:
            if active.read_text(encoding="utf-8").strip() == name:
                active.unlink()
        except OSError:
            pass
        return {"name": name}

    def update_machine(self, payload: dict) -> dict:
        name = clean_name(payload.get("name"))
        if self._domain_state(name) not in ("shut off", "tanımsız"):
            raise RequestError("CPU/RAM ayarı için makine kapalı veya hibernate olmalı")
        path = self.machine_root / f"{name}.json"
        try:
            original_text = path.read_text(encoding="utf-8")
            config = json.loads(original_text)
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestError("makine kaydı bulunamadı") from exc
        memory, vcpus = self._requested_resources(payload.get("memory_mib"), payload.get("vcpus"))
        config.update(
            {
                "title": clean_title(payload.get("title")),
                "memory_mib": memory,
                "vcpus": vcpus,
            }
        )
        temporary = path.with_suffix(".json.new")
        try:
            temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o640)
            os.replace(temporary, path)
            self._define_machine(name, config)
        except Exception:
            path.write_text(original_text, encoding="utf-8")
            os.chmod(path, 0o640)
            raise
        return {"machine": self._read_machine(path)}

    def power(self, payload: dict) -> dict:
        operation = str(payload.get("operation", ""))
        if payload.get("confirm") != "OBSI":
            raise RequestError("güç işlemi onaylanmadı")
        if operation not in ("reboot", "poweroff"):
            raise RequestError("geçersiz güç işlemi")
        run(["systemctl", operation], timeout=10)
        return {"operation": operation}

    def diagnostics(self, _payload: dict) -> dict:
        result = run(
            [
                "journalctl",
                "--no-pager",
                "--lines=250",
                "--output=short-iso",
                "--unit=obsi-core.service",
                "--unit=obsi-storaged.service",
                "--unit=obsi-inputd.service",
                "--unit=libvirtd.service",
            ],
            timeout=20,
            check=False,
        )
        return {"log": result.stdout[-200000:]}

    def dispatch(self, action: str, payload: dict) -> dict:
        handlers = {
            "overview": lambda _: self.overview(),
            "machine.create": self.create_machine,
            "machine.start": self.start_machine,
            "machine.hibernate": self.hibernate_machine,
            "machine.delete": self.delete_machine,
            "machine.update": self.update_machine,
            "template.import": self.import_template,
            "input.configure": self.configure_input,
            "gpu.configure": self.configure_gpu,
            "usb.configure": self.configure_usb,
            "network.scan": self.network_scan,
            "network.connect": self.network_connect,
            "diagnostics": self.diagnostics,
            "power": self.power,
        }
        handler = handlers.get(action)
        if handler is None:
            raise RequestError("bilinmeyen OBSI Core işlemi")
        with self.lock:
            return handler(payload)


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            self._reply(False, error="istek çok büyük")
            return
        try:
            message = json.loads(raw)
            if not isinstance(message, dict) or not isinstance(message.get("payload", {}), dict):
                raise RequestError("geçersiz istek")
            data = self.server.core.dispatch(str(message.get("action", "")), message.get("payload", {}))  # type: ignore[attr-defined]
            self._reply(True, data=data)
        except (RequestError, ValueError, OSError, json.JSONDecodeError) as exc:
            self._reply(False, error=str(exc))
        except Exception:
            self.server.logger.exception("unhandled request error")  # type: ignore[attr-defined]
            self._reply(False, error="OBSI Core beklenmeyen bir hata yaşadı")

    def _reply(self, ok: bool, **payload: object) -> None:
        self.wfile.write((json.dumps({"ok": ok, **payload}, ensure_ascii=False) + "\n").encode())


UnixServerBase = getattr(socketserver, "ThreadingUnixStreamServer", socketserver.ThreadingTCPServer)


class Server(UnixServerBase):
    daemon_threads = True
    allow_reuse_address = True


def serve(socket_path: Path, core: Core) -> None:
    import grp
    import logging

    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = Server(str(socket_path), Handler)
    server.core = core  # type: ignore[attr-defined]
    server.logger = logging.getLogger("obsi-core")  # type: ignore[attr-defined]
    group = grp.getgrnam("obsi").gr_gid
    os.chown(socket_path, 0, group)
    os.chmod(socket_path, 0o660)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        socket_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, default=Path("/run/obsi/core.sock"))
    args = parser.parse_args()
    import logging

    logging.basicConfig(level=logging.INFO, format="obsi-core: %(message)s")
    if os.geteuid() != 0:
        raise SystemExit("obsi-core must run as root")
    serve(args.socket, Core())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
