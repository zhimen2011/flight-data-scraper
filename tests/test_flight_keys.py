import unittest
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flight_keys import build_flight_key, display_datetime_from_key, parse_flight_key


class FlightKeyTests(unittest.TestCase):
    def test_builds_key_with_takeoff_time(self):
        self.assertEqual(
            "I99852_2026-05-02_134500",
            build_flight_key("i99852", "2026-05-02", "2026-05-02 13:45:00"),
        )

    def test_parses_occurrence_key_with_suffix(self):
        parsed = parse_flight_key("I99852_2026-05-02_134500_2")

        self.assertEqual("I99852", parsed.flight)
        self.assertEqual("2026-05-02", parsed.date)
        self.assertEqual("134500", parsed.time_token)
        self.assertEqual("2", parsed.suffix)
        self.assertEqual("2026-05-02_134500_2", parsed.date_key)

    def test_displays_datetime_key(self):
        self.assertEqual(
            "2026-05-02 13:45:00",
            display_datetime_from_key("I99852_2026-05-02_134500"),
        )


if __name__ == "__main__":
    unittest.main()
