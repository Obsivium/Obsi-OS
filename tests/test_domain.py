import unittest
from xml.etree import ElementTree as ET

from obsi.domain import DomainSpec, build_domain_xml


class DomainTests(unittest.TestCase):
    def test_headless_passthrough_domain(self):
        xml = build_domain_xml(
            DomainSpec(
                name="gaming",
                title="Gaming",
                disk="/dev/obsi-vg/vm-gaming",
                memory_mib=16384,
                vcpus=8,
                gpu_devices=("0000:01:00.0", "0000:01:00.1"),
                usb_devices=(("046d", "c539"),),
            )
        )
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext("name"), "gaming")
        self.assertEqual(root.find("devices/video/model").attrib["type"], "none")
        self.assertEqual(len(root.findall("devices/hostdev[@type='pci']")), 2)
        self.assertIsNone(root.find("devices/graphics"))
        self.assertEqual(root.find("devices/input[@type='evdev']/source").attrib["dev"], "/dev/input/by-id/obsi-guest-keyboard")
        self.assertEqual(root.find("devices/hostdev[@type='usb']/source/vendor").attrib["id"], "0x046d")

    def test_spice_fallback_domain(self):
        root = ET.fromstring(
            build_domain_xml(DomainSpec(name="coding", title="Coding", disk="/dev/obsi-vg/vm-coding"))
        )
        self.assertEqual(root.find("devices/graphics").attrib["type"], "spice")
        self.assertEqual(root.find("devices/video/model").attrib["type"], "virtio")

    def test_rejects_device_outside_pool(self):
        with self.assertRaises(ValueError):
            build_domain_xml(DomainSpec(name="bad", title="Bad", disk="/dev/sda"))

    def test_rejects_unsafe_name(self):
        with self.assertRaises(ValueError):
            build_domain_xml(DomainSpec(name="Bad Name", title="Bad", disk="/dev/obsi-vg/vm-bad"))


if __name__ == "__main__":
    unittest.main()
