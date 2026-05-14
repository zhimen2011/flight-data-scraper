import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_docx_report import format_altitude, format_fl_deviation
from local_report import _flight_item_sort_key, _format_fl, build_remarks, build_statistics_rows


class LocalReportTests(unittest.TestCase):
    def test_flight_item_sort_key_orders_by_date_before_flight(self):
        items = [
            ("I99858_2026-04-16_080000", {}),
            ("I99857_2026-04-14", {}),
            ("Z99999_2026-04-16_080000", {}),
            ("A00001_2026-04-16_090000", {}),
        ]

        ordered = [key for key, _ in sorted(items, key=_flight_item_sort_key)]

        self.assertEqual(
            ordered,
            [
                "I99857_2026-04-14",
                "I99858_2026-04-16_080000",
                "Z99999_2026-04-16_080000",
                "A00001_2026-04-16_090000",
            ],
        )

    def test_non_china_region_uses_whole_thousand_flight_levels(self):
        self.assertEqual("FL350", _format_fl(35100, {}, {"region": "Kazakhstan"}))
        self.assertEqual("FL330", _format_fl(33100, {}, {"region": "Kazakhstan"}))
        self.assertEqual("FL350", format_altitude(35100, "Kazakhstan"))
        self.assertNotIn("FL351", format_fl_deviation(39100, 35100, "Kazakhstan"))

    def test_china_region_keeps_caac_metric_flight_levels(self):
        self.assertEqual("FL351", _format_fl(35100, {}, {"region": "CN"}))
        self.assertIn("FL351", format_altitude(35100, "CN"))

    def test_build_remarks_uses_actual_level_and_direction(self):
        result = {
            "warnings": [
                {
                    "region": "蒙古",
                    "actual_alt": 30100,
                    "plan_alt": 32100,
                    "avg_dev_ft": -2000,
                }
            ]
        }

        remarks = build_remarks(result)

        self.assertIn("蒙古区域高度 FL300 低于计划高度一个高度层", remarks)

    def test_statistics_rows_fallback_to_analysis_metadata(self):
        analysis = {
            "flights": {
                "I98833_2026-03-08": {
                    "metadata": {
                        "dep_airport": "ZHEC",
                        "arr_airport": "EBBR",
                        "dep_time": "2026-03-08 08:09:00",
                    },
                    "warnings": [],
                }
            }
        }

        rows = build_statistics_rows(analysis)

        self.assertEqual(rows[0]["date"], "2026/03/08")
        self.assertEqual(rows[0]["dep"], "鄂州")
        self.assertEqual(rows[0]["arr"], "布鲁塞尔")

    def test_build_remarks_uses_nearest_valid_flight_level(self):
        result = {
            "warnings": [
                {
                    "region": "俄罗斯",
                    "actual_alt": 33900,
                    "plan_alt": 31900,
                    "avg_dev_ft": 2000,
                }
            ]
        }

        remarks = build_remarks(result)

        self.assertIn("俄罗斯区域高度 FL340 高于计划高度一个高度层", remarks)

    def test_build_remarks_uses_country_for_single_europe_event(self):
        result = {
            "warnings": [
                {
                    "region": "欧洲",
                    "countries": ["Germany"],
                    "actual_alt": 30100,
                    "plan_alt": 32100,
                    "avg_dev_ft": -2000,
                }
            ]
        }

        remarks = build_remarks(result)

        self.assertIn("德国区域高度 FL300 低于计划高度一个高度层", remarks)

    def test_build_remarks_lists_countries_for_long_europe_event(self):
        result = {
            "warnings": [
                {
                    "region": "欧洲",
                    "countries": ["Poland", "Germany"],
                    "actual_alt": 30100,
                    "plan_alt": 32100,
                    "avg_dev_ft": -2000,
                }
            ]
        }

        remarks = build_remarks(result)

        self.assertIn("欧洲区域（波兰、德国）高度 FL300 低于计划高度一个高度层", remarks)

    def test_build_remarks_adds_domestic_waypoint_context(self):
        result = {
            "plan_waypoints": [
                {"name": "A", "dist": 90},
                {"name": "B", "dist": 120},
                {"name": "C", "dist": 150},
            ],
            "deviation_data": [
                {"dis": 118, "alt_dev": -1500},
                {"dis": 121, "alt_dev": -2200},
            ],
            "warnings": [
                {
                    "region": "国内段",
                    "start_dist": 115,
                    "end_dist": 125,
                    "actual_alt": 30100,
                    "plan_alt": 32100,
                    "avg_dev_ft": -2000,
                }
            ],
        }

        remarks = build_remarks(result)

        self.assertIn("国内段（B点附近）高度 FL301 低于计划高度一个高度层", remarks)

    def test_build_remarks_adds_descent_analysis_context(self):
        result = {
            "warnings": [],
            "descent_analysis": {
                "is_premature": True,
                "is_domestic_descent": True,
                "actual_descent_start_wp": "TCH",
                "descent_diff_nm": 42.4,
            },
        }

        remarks = build_remarks(result)

        self.assertIn("国内段（TCH点附近）较计划提前下降约42nm", remarks)

    def test_build_remarks_skips_non_domestic_descent(self):
        result = {
            "warnings": [],
            "descent_analysis": {
                "is_premature": True,
                "is_domestic_descent": False,
                "actual_descent_start_wp": "TCH",
                "descent_diff_nm": 42.4,
            },
        }

        remarks = build_remarks(result)

        self.assertEqual("", remarks)


if __name__ == "__main__":
    unittest.main()
