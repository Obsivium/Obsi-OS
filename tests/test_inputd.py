import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obsi import inputd


class HotkeyFilterTests(unittest.TestCase):
    def test_alt_f12_is_not_forwarded_and_alt_is_released(self):
        state = inputd.HotkeyFilter()
        alt_down = state.process(inputd.EV_KEY, inputd.KEY_LEFTALT, 1)
        hotkey = state.process(inputd.EV_KEY, inputd.KEY_F12, 1)
        f12_up = state.process(inputd.EV_KEY, inputd.KEY_F12, 0)
        alt_up = state.process(inputd.EV_KEY, inputd.KEY_LEFTALT, 0)

        self.assertEqual(alt_down.forward, [(inputd.EV_KEY, inputd.KEY_LEFTALT, 1)])
        self.assertTrue(hotkey.trigger)
        self.assertEqual(hotkey.forward, [])
        self.assertEqual(hotkey.synthetic, [(inputd.EV_KEY, inputd.KEY_LEFTALT, 0)])
        self.assertEqual(f12_up.forward, [])
        self.assertEqual(alt_up.forward, [])

    def test_plain_f12_is_forwarded(self):
        state = inputd.HotkeyFilter()
        result = state.process(inputd.EV_KEY, inputd.KEY_F12, 1)
        self.assertFalse(result.trigger)
        self.assertEqual(result.forward, [(inputd.EV_KEY, inputd.KEY_F12, 1)])

    def test_unrelated_events_are_forwarded(self):
        state = inputd.HotkeyFilter()
        result = state.process(inputd.EV_KEY, 30, 1)
        self.assertEqual(result.forward, [(inputd.EV_KEY, 30, 1)])


if __name__ == "__main__":
    unittest.main()
