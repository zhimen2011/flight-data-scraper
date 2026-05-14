import os
import sys
import shutil
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyze_flight import CSV_DIR
from flight_server import BASE_DIR, archive_active_csv_data, list_active_csv_files

TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_archive")


class FlightServerArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(TMP_ROOT, exist_ok=True)

    def make_test_dir(self):
        path = os.path.join(TMP_ROOT, f"{self._testMethodName}_{uuid.uuid4().hex}")
        os.makedirs(path)
        self.addCleanup(lambda: shutil.rmtree(path, ignore_errors=True))
        return path

    def test_archive_moves_current_csv_and_metadata_only(self):
        tmp = self.make_test_dir()
        csv_dir = os.path.join(tmp, "flight_csv")
        archive_root = os.path.join(tmp, "flight_csv_archive")
        os.makedirs(csv_dir)
        old_files = [
            "I99851_2026-04-10_112603_plan_track.csv",
            "I99851_2026-04-10_112603_actual_track.csv",
            "metadata.json",
        ]
        for name in old_files:
            with open(os.path.join(csv_dir, name), "w", encoding="utf-8") as f:
                f.write("x")
        with open(os.path.join(csv_dir, "keep.txt"), "w", encoding="utf-8") as f:
            f.write("keep")

        result = archive_active_csv_data(csv_dir, archive_root)

        self.assertEqual(3, result["archived"])
        self.assertTrue(os.path.isdir(result["archive_dir"]))
        for name in old_files:
            self.assertFalse(os.path.exists(os.path.join(csv_dir, name)))
            self.assertTrue(os.path.exists(os.path.join(result["archive_dir"], name)))
        self.assertTrue(os.path.exists(os.path.join(csv_dir, "keep.txt")))

    def test_list_active_csv_files_ignores_subdirectories(self):
        tmp = self.make_test_dir()
        csv_dir = os.path.join(tmp, "flight_csv")
        os.makedirs(csv_dir)
        os.makedirs(os.path.join(csv_dir, "old"))
        with open(os.path.join(csv_dir, "I99851_2026-04-10_112603_plan_track.csv"), "w", encoding="utf-8") as f:
            f.write("x")
        with open(os.path.join(csv_dir, "I99851_2026-04-10_112603_actual_track.csv"), "w", encoding="utf-8") as f:
            f.write("x")
        with open(os.path.join(csv_dir, "old", "I99852_2026-04-10_011316_plan_track.csv"), "w", encoding="utf-8") as f:
            f.write("x")
        with open(os.path.join(csv_dir, "old", "I99852_2026-04-10_011316_actual_track.csv"), "w", encoding="utf-8") as f:
            f.write("x")

        files = list_active_csv_files(csv_dir)

        self.assertEqual(["I99851_2026-04-10_112603"], [item["key"] for item in files])

    def test_list_active_csv_files_requires_actual_track_pair(self):
        tmp = self.make_test_dir()
        csv_dir = os.path.join(tmp, "flight_csv")
        os.makedirs(csv_dir)
        with open(os.path.join(csv_dir, "I99851_2026-04-10_112603_plan_track.csv"), "w", encoding="utf-8") as f:
            f.write("x")

        files = list_active_csv_files(csv_dir)

        self.assertEqual([], files)

    def test_server_runtime_csv_dir_matches_analyzer_dir(self):
        self.assertEqual(os.path.join(BASE_DIR, "flight_csv"), CSV_DIR)


if __name__ == "__main__":
    unittest.main()
