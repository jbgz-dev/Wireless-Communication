import argparse
import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

import fs_tools as fs


class WalkExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session_dir = self.root / "sessions" / "walk-test"
        self.session_dir.mkdir(parents=True)
        self.meta = {"session_id": "walk-test", "status": "complete",
                     "labels": {"mode": "walk", "building": "T8", "floor": 3,
                                "point_id": "TW01", "split": "train"},
                     "stats": {"wifi_scans_fresh": 2},
                     "walk": {"nodes_expected": 3, "nodes_recorded": 3, "route_complete": True}}
        self.snapshot = {"route_id": "TW01", "direction": "forward", "nodes": [
            {"id": name, "route_order": order} for order, name in enumerate(["A", "B", "C"], 1)]}
        self.rows = []
        for i, node in enumerate(self.snapshot["nodes"], 1):
            row = dict.fromkeys(fs.NODE_FIELDS, "")
            row.update(session_id="walk-test", building="T8", floor="3", route_id="TW01",
                       direction="forward", mark_id=str(i), node_index=str(i), node_id=node["id"],
                       route_order=str(node["route_order"]),
                       t_wall_ms=str(10000 + i * 1000), t_elapsed_ms=str(i * 1000),
                       source="auto_start" if i == 1 else "manual")
            self.rows.append(row)
        self.marks = [{"action": "reached", **r} for r in self.rows]
        self.save()

    def write_csv(self, filename, fields, rows):
        with (self.session_dir / filename).open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def save(self):
        (self.session_dir / "meta.json").write_text(json.dumps(self.meta), encoding="utf-8")
        (self.session_dir / "route_snapshot.json").write_text(json.dumps(self.snapshot), encoding="utf-8")
        self.write_csv("node_visits.csv", fs.NODE_FIELDS, self.rows)
        self.write_csv("node_marks.csv", ["action"] + fs.NODE_FIELDS, self.marks)
        self.session = fs.Session(self.session_dir, self.meta)

    def test_complete_route_with_sparse_wifi_is_retained(self):
        errors, warns = fs.check_walk(self.session)
        self.assertEqual(errors, [])
        self.assertTrue(warns)
        output = self.root / "combined.csv"
        with contextlib.redirect_stdout(io.StringIO()):
            result = fs.cmd_nodes(argparse.Namespace(dirs=[self.root], output=output, include_incomplete=False))
        self.assertEqual(result, 0)
        rows = fs.read_csv(output)
        self.assertEqual([r["node_id"] for r in rows], ["A", "B", "C"])
        self.assertEqual(rows[1]["t_elapsed_ms"], "2000")
        self.assertEqual(rows[1]["split"], "train")

    def test_undo_and_rerecord_excludes_wrong_arrival(self):
        old = dict(self.rows[-1])
        self.rows[-1] = {**old, "mark_id": "4", "t_elapsed_ms": "5000", "t_wall_ms": "15000"}
        self.marks.extend([{"action": "undo", **old, "t_elapsed_ms": "4000", "t_wall_ms": "14000"},
                           {"action": "reached", **self.rows[-1]}])
        self.save()
        self.assertEqual(fs.check_walk(self.session)[0], [])

    def test_stale_effective_table_is_rejected(self):
        self.marks.append({"action": "undo", **self.rows[-1]})
        self.save()
        self.assertTrue(fs.check_walk(self.session)[0])

    def test_complete_status_cannot_hide_missing_endpoint(self):
        self.rows.pop()
        self.marks.pop()
        self.meta["walk"].update(nodes_recorded=2, route_complete=False)
        self.save()
        self.assertTrue(fs.check_walk(self.session)[0])
        self.meta["status"] = "incomplete"
        self.save()
        self.assertEqual(fs.check_walk(self.session)[0], [])

    def test_wrong_node_order_is_rejected(self):
        self.rows[1]["node_id"] = "C"
        self.save()
        self.assertTrue(fs.check_walk(self.session)[0])

    def test_reverse_order(self):
        self.snapshot["direction"] = "reverse"
        self.snapshot["nodes"] = list(reversed(self.snapshot["nodes"]))
        for i, (node, row) in enumerate(zip(self.snapshot["nodes"], self.rows)):
            row.update(direction="reverse", node_id=node["id"], route_order=str(node["route_order"]))
        self.marks = [{"action": "reached", **r} for r in self.rows]
        self.save()
        self.assertEqual(fs.check_walk(self.session)[0], [])

    def test_merge_retains_time_alignment_fields_and_cached_scans_are_excluded(self):
        fields = ["scan_id", "t_wall_ms", "t_elapsed_ms", "fresh", "n_ap", "bssid", "rssi",
                  "freq_mhz", "age_ms", "results_updated"]
        fresh = dict(zip(fields, ["0", "12000", "2000", "1", "1", "ap", "-60", "2412", "100", "1"]))
        self.write_csv("wifi.csv", fields, [fresh, {**fresh, "scan_id": "1", "fresh": "0"}])
        output = self.root / "wifi_combined.csv"
        with contextlib.redirect_stdout(io.StringIO()):
            fs.cmd_merge(argparse.Namespace(dirs=[self.root], output=output, include_incomplete=False, modes="walk"))
        rows = fs.read_csv(output)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["t_elapsed_ms"], "2000")
        self.assertEqual(rows[0]["age_ms"], "100")


if __name__ == "__main__":
    unittest.main()
