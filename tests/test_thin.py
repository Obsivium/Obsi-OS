import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from obsi.thin import PolicyLevel, evaluate_policy, parse_map_allocated, parse_pool_report


def report(data: str, metadata: str, when_full: str = "error") -> str:
    return json.dumps(
        {
            "report": [
                {
                    "lv": [
                        {
                            "vg_name": "obsi-vg",
                            "lv_name": "obsi-thinpool",
                            "lv_size": "987654321",
                            "data_percent": data,
                            "metadata_percent": metadata,
                            "whenfull": when_full,
                            "lv_attr": "twi-aotz--",
                        }
                    ]
                }
            ]
        }
    )


class ThinPolicyTests(unittest.TestCase):
    def test_ok_below_warning(self):
        level, _ = evaluate_policy(84.99, 20)
        self.assertEqual(level, PolicyLevel.OK)

    def test_warning_at_85(self):
        level, _ = evaluate_policy(85, 20)
        self.assertEqual(level, PolicyLevel.WARN)

    def test_critical_at_92(self):
        level, _ = evaluate_policy(20, 92)
        self.assertEqual(level, PolicyLevel.CRITICAL)

    def test_deny_at_96_for_either_dimension(self):
        self.assertEqual(evaluate_policy(96, 1)[0], PolicyLevel.DENY)
        self.assertEqual(evaluate_policy(1, 96)[0], PolicyLevel.DENY)

    def test_report_parsing(self):
        status = parse_pool_report(
            report("86.25", "12.50"),
            expected_vg="obsi-vg",
            expected_pool="obsi-thinpool",
        )
        self.assertEqual(status.level, PolicyLevel.WARN)
        self.assertEqual(status.size_bytes, 987654321)
        self.assertTrue(status.can_start)
        self.assertEqual(status.when_full, "error")
        self.assertEqual(status.used_bytes, int(987654321 * 0.8625))

    def test_less_than_percent_format_is_accepted(self):
        status = parse_pool_report(
            report("<0.01", "<0.01"),
            expected_vg="obsi-vg",
            expected_pool="obsi-thinpool",
        )
        self.assertEqual(status.level, PolicyLevel.OK)

    def test_missing_pool_fails_closed(self):
        with self.assertRaises(ValueError):
            parse_pool_report(
                '{"report":[{"lv":[]}]}',
                expected_vg="obsi-vg",
                expected_pool="obsi-thinpool",
            )

    def test_needs_check_health_flag_fails_closed(self):
        payload = json.loads(report("10", "10"))
        payload["report"][0]["lv"][0]["lv_attr"] = "twi-cotz--"
        status = parse_pool_report(
            json.dumps(payload),
            expected_vg="obsi-vg",
            expected_pool="obsi-thinpool",
        )
        self.assertEqual(status.level, PolicyLevel.FAILED)
        self.assertFalse(status.can_start)

    def test_out_of_data_health_flag_fails_closed(self):
        payload = json.loads(report("99", "10"))
        payload["report"][0]["lv"][0]["lv_attr"] = "twi-aotzD-"
        status = parse_pool_report(
            json.dumps(payload),
            expected_vg="obsi-vg",
            expected_pool="obsi-thinpool",
        )
        self.assertEqual(status.level, PolicyLevel.FAILED)

    def test_queue_when_full_is_at_least_warning(self):
        status = parse_pool_report(
            report("10", "10", when_full="queue"),
            expected_vg="obsi-vg",
            expected_pool="obsi-thinpool",
        )
        self.assertEqual(status.level, PolicyLevel.WARN)

    def test_qemu_map_json(self):
        payload = json.dumps(
            [
                {"start": 0, "length": 100, "data": True, "zero": False},
                {"start": 100, "length": 200, "data": False, "zero": True},
                {"start": 300, "length": 50, "data": True, "zero": True},
            ]
        )
        self.assertEqual(parse_map_allocated(payload), 100)
        with self.assertRaises(ValueError):
            parse_map_allocated('{"not":"a list"}')


if __name__ == "__main__":
    unittest.main()
