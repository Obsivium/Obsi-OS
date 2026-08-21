import unittest

from obsi.core import RequestError, bounded_int, clean_name, clean_title, split_nmcli


class CoreValidationTests(unittest.TestCase):
    def test_machine_name_is_normalized(self):
        self.assertEqual(clean_name(" Gaming-01 "), "gaming-01")

    def test_shell_metacharacters_are_rejected(self):
        for name in ("gaming;reboot", "../etc", "name with space", "$(id)"):
            with self.assertRaises(RequestError):
                clean_name(name)

    def test_title_control_character_rejected(self):
        with self.assertRaises(RequestError):
            clean_title("Gaming\nRoot")

    def test_bounded_integer(self):
        self.assertEqual(bounded_int("8", 1, 16, "cpu"), 8)
        with self.assertRaises(RequestError):
            bounded_int(32, 1, 16, "cpu")

    def test_nmcli_escaped_separator(self):
        self.assertEqual(split_nmcli(r"wlan0:wifi:connected:Cafe\: 5G"), ["wlan0", "wifi", "connected", "Cafe: 5G"])


if __name__ == "__main__":
    unittest.main()
