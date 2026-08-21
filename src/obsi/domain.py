"""Secure libvirt domain construction for OBSI workspaces."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
PCI_RE = re.compile(r"^0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
USB_ID_RE = re.compile(r"^[0-9a-f]{4}$")


@dataclass(frozen=True)
class DomainSpec:
    name: str
    title: str
    disk: str
    memory_mib: int = 8192
    vcpus: int = 4
    firmware: str = "/usr/share/OVMF/OVMF_CODE_4M.fd"
    nvram_template: str = "/usr/share/OVMF/OVMF_VARS_4M.fd"
    gpu_devices: tuple[str, ...] = field(default_factory=tuple)
    usb_devices: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def validate(self) -> None:
        if not NAME_RE.fullmatch(self.name):
            raise ValueError("invalid machine name")
        if not self.title.strip() or len(self.title) > 64:
            raise ValueError("title must be 1-64 characters")
        if not self.disk.startswith("/dev/obsi-vg/vm-"):
            raise ValueError("disk is outside the OBSI volume group")
        if not 1024 <= self.memory_mib <= 1048576:
            raise ValueError("memory must be between 1 GiB and 1 TiB")
        if not 1 <= self.vcpus <= 256:
            raise ValueError("vCPU count must be between 1 and 256")
        if any(not PCI_RE.fullmatch(device) for device in self.gpu_devices):
            raise ValueError("invalid PCI address")
        if any(not USB_ID_RE.fullmatch(vendor) or not USB_ID_RE.fullmatch(product) for vendor, product in self.usb_devices):
            raise ValueError("invalid USB vendor/product id")


def _text(parent: ET.Element, name: str, value: str, **attrs: str) -> ET.Element:
    node = ET.SubElement(parent, name, attrs)
    node.text = value
    return node


def _pci_address(parent: ET.Element, address: str) -> None:
    domain, bus, slot_function = address.split(":")
    slot, function = slot_function.split(".")
    ET.SubElement(
        parent,
        "address",
        type="pci",
        domain=f"0x{domain}",
        bus=f"0x{bus}",
        slot=f"0x{slot}",
        function=f"0x{function}",
    )


def build_domain_xml(spec: DomainSpec) -> str:
    spec.validate()
    root = ET.Element("domain", type="kvm")
    _text(root, "name", spec.name)
    _text(root, "title", spec.title)
    _text(root, "memory", str(spec.memory_mib), unit="MiB")
    _text(root, "currentMemory", str(spec.memory_mib), unit="MiB")
    _text(root, "vcpu", str(spec.vcpus), placement="static")

    os_node = ET.SubElement(root, "os", firmware="efi")
    _text(os_node, "type", "hvm", arch="x86_64", machine="q35")
    _text(os_node, "loader", spec.firmware, readonly="yes", type="pflash")
    _text(os_node, "nvram", f"/var/lib/libvirt/qemu/nvram/{spec.name}_VARS.fd", template=spec.nvram_template)
    ET.SubElement(os_node, "boot", dev="hd")

    features = ET.SubElement(root, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")
    hyperv = ET.SubElement(features, "hyperv", mode="custom")
    ET.SubElement(hyperv, "relaxed", state="on")
    ET.SubElement(hyperv, "vapic", state="on")
    ET.SubElement(hyperv, "spinlocks", state="on", retries="8191")
    ET.SubElement(hyperv, "vpindex", state="on")
    ET.SubElement(hyperv, "synic", state="on")
    ET.SubElement(hyperv, "stimer", state="on")
    kvm = ET.SubElement(features, "kvm")
    ET.SubElement(kvm, "hidden", state="on")
    ET.SubElement(features, "vmport", state="off")
    ET.SubElement(root, "cpu", mode="host-passthrough", check="none", migratable="off")
    ET.SubElement(root, "clock", offset="localtime")
    ET.SubElement(root, "on_poweroff").text = "destroy"
    ET.SubElement(root, "on_reboot").text = "restart"
    ET.SubElement(root, "on_crash").text = "destroy"

    devices = ET.SubElement(root, "devices")
    ET.SubElement(devices, "emulator").text = "/usr/bin/qemu-system-x86_64"
    disk = ET.SubElement(devices, "disk", type="block", device="disk", snapshot="external")
    ET.SubElement(disk, "driver", name="qemu", type="raw", cache="none", io="native", discard="unmap")
    ET.SubElement(disk, "source", dev=spec.disk)
    ET.SubElement(disk, "target", dev="vda", bus="virtio")
    ET.SubElement(disk, "boot", order="1")

    interface = ET.SubElement(devices, "interface", type="network")
    ET.SubElement(interface, "source", network="default")
    ET.SubElement(interface, "model", type="virtio")
    channel = ET.SubElement(devices, "channel", type="unix")
    ET.SubElement(channel, "target", type="virtio", name="org.qemu.guest_agent.0")
    ET.SubElement(devices, "input", type="keyboard", bus="virtio")
    ET.SubElement(devices, "input", type="mouse", bus="virtio")
    evdev = ET.SubElement(devices, "input", type="evdev")
    ET.SubElement(evdev, "source", dev="/dev/input/by-id/obsi-guest-keyboard", grab="all", repeat="on")
    tpm = ET.SubElement(devices, "tpm", model="tpm-crb")
    ET.SubElement(tpm, "backend", type="emulator", version="2.0")

    if spec.gpu_devices:
        ET.SubElement(devices, "video").append(ET.Element("model", type="none"))
        for address in spec.gpu_devices:
            hostdev = ET.SubElement(devices, "hostdev", mode="subsystem", type="pci", managed="yes")
            source = ET.SubElement(hostdev, "source")
            _pci_address(source, address)
    else:
        graphics = ET.SubElement(devices, "graphics", type="spice", autoport="yes", listen="127.0.0.1")
        ET.SubElement(graphics, "listen", type="address", address="127.0.0.1")
        video = ET.SubElement(devices, "video")
        ET.SubElement(video, "model", type="virtio", heads="1", primary="yes")

    for vendor, product in spec.usb_devices:
        hostdev = ET.SubElement(devices, "hostdev", mode="subsystem", type="usb", managed="yes")
        source = ET.SubElement(hostdev, "source")
        ET.SubElement(source, "vendor", id=f"0x{vendor}")
        ET.SubElement(source, "product", id=f"0x{product}")

    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)
