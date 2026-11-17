"""Compare D1 with the Nov. 11 temporal revisit (D2 here) at matching north-facing points."""

import argparse
from collections import defaultdict
import csv
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
from statistics import median
import sys
from zipfile import ZipFile


STAGE = Path(__file__).resolve().parent
ROOT = STAGE.parent
sys.path.insert(0, str(ROOT))
from d2_validation.mobile_reference import prepare_scan


SCAN_CONFIG = {"max_age_ms": 10000, "clip_rssi": [-100, -20]}
FOCUS_POINTS = {"T8-F5-F-TR01", "T8-F6-C-TR01"}


def read_north_sessions(archive, points=None):
    sessions = {}
    with ZipFile(archive) as source:
        for name in sorted(source.namelist()):
            entry = PurePosixPath(name)
            if entry.name != "meta.json" or "__MACOSX" in entry.parts:
                continue
            meta = json.loads(source.read(name).decode("utf-8-sig"))
            label = meta["labels"]
            point = label["point_id"]
            if label["orientation"] != "N" or (points is not None and point not in points):
                continue
            if (point in sessions or meta["status"] != "complete"
                    or label["building"] != "T8" or label["mode"] != "static"):
                raise ValueError(f"Expected one complete T8 static N session per point in {archive}: {point}")

            scans = defaultdict(list)
            wifi = source.read(str(entry.with_name("wifi.csv"))).decode("utf-8-sig")
            for row in csv.DictReader(io.StringIO(wifi)):
                scans[int(row["scan_id"])].append(row)
            eligible = [prepare_scan(rows, SCAN_CONFIG) for rows in scans.values()]
            eligible = [scan for scan in eligible if scan]
            if not eligible:
                raise ValueError(f"No eligible Wi-Fi scans in {archive}: {point}")
            unique = {tuple(sorted(scan.items())): scan for scan in eligible}

            rssi_by_bssid = defaultdict(list)
            for scan in unique.values():
                for bssid, rssi in scan.items():
                    rssi_by_bssid[bssid].append(rssi)
            date_rssi = {bssid: median(values) for bssid, values in rssi_by_bssid.items()}

            device = meta["device"]
            stats = {
                "session_id": meta["session_id"],
                "start_local": meta["start_local"],
                "collector": label["collector"],
                "device": f"{device['manufacturer']} {device['model']}",
                "android_release": device["android_release"],
                "raw_scans": len(scans),
                "eligible_scans": len(eligible),
                "unique_scans": len(unique),
                "median_scan_aps": median(map(len, unique.values())),
                "union_aps": len(date_rssi),
            }
            sessions[point] = (meta, stats, date_rssi, unique.keys())
    return sessions


def compare_pair(point, d1, d2):
    meta1, stats1, rssi1, fingerprints1 = d1
    meta2, stats2, rssi2, fingerprints2 = d2
    label1, label2 = meta1["labels"], meta2["labels"]
    if (any(label1[key] != label2[key] for key in ("floor", "zone", "posture"))
            or meta1["timezone"] != meta2["timezone"]
            or datetime.fromisoformat(stats1["start_local"]).date() >= datetime.fromisoformat(stats2["start_local"]).date()):
        raise ValueError(f"Expected matching point labels and a later D2 date: {point}")

    shared = rssi1.keys() & rssi2.keys()
    union = rssi1.keys() | rssi2.keys()
    shifts = [rssi2[bssid] - rssi1[bssid] for bssid in shared]
    return {
        "point_id": point,
        "floor": label1["floor"],
        "anchor_zone": label1["zone"],
        "orientation": label1["orientation"],
        "posture": label1["posture"],
        "timezone": meta1["timezone"],
        **{f"d1_{key}": value for key, value in stats1.items()},
        **{f"d2_{key}": value for key, value in stats2.items()},
        "same_collector": stats1["collector"].casefold() == stats2["collector"].casefold(),
        "same_device_model": stats1["device"] == stats2["device"],
        "median_scan_aps_change": stats2["median_scan_aps"] - stats1["median_scan_aps"],
        "union_aps_change": len(rssi2) - len(rssi1),
        "shared_aps": len(shared),
        "union_aps": len(union),
        "bssid_jaccard": len(shared) / len(union),
        "median_signed_rssi_change_db": median(shifts) if shifts else None,
        "median_absolute_rssi_change_db": median(map(abs, shifts)) if shifts else None,
        "exact_shared_fingerprints": len(fingerprints1 & fingerprints2),
    }


def summarize(pairs, archives):
    ranked = {
        "lowest_jaccard": sorted(pairs, key=lambda row: (row["bssid_jaccard"], row["point_id"]))[:3],
        "largest_scan_ap_loss": sorted(
            (row for row in pairs if row["median_scan_aps_change"] < 0),
            key=lambda row: (row["median_scan_aps_change"], row["point_id"]),
        )[:3],
        "largest_scan_ap_gain": sorted(
            (row for row in pairs if row["median_scan_aps_change"] > 0),
            key=lambda row: (-row["median_scan_aps_change"], row["point_id"]),
        )[:3],
    }
    rssi_pairs = [row for row in pairs if row["median_absolute_rssi_change_db"] is not None]
    if rssi_pairs:
        maximum = max(row["median_absolute_rssi_change_db"] for row in rssi_pairs)
        ranked["largest_absolute_rssi_change"] = [
            row for row in rssi_pairs if row["median_absolute_rssi_change_db"] == maximum
        ]
    extremes = {reason: [row["point_id"] for row in rows] for reason, rows in ranked.items()}
    for row in pairs:
        point = row["point_id"]
        flags = [reason for reason, points in extremes.items() if point in points]
        if point in FOCUS_POINTS:
            flags.insert(0, "selected_for_review")
        row["flags"] = "|".join(flags)

    median_fields = (
        "d1_median_scan_aps", "d2_median_scan_aps", "median_scan_aps_change",
        "d1_union_aps", "d2_union_aps", "union_aps_change", "shared_aps", "union_aps", "bssid_jaccard",
    )
    point_medians = {key: median(row[key] for row in pairs) for key in median_fields}
    for key in ("median_signed_rssi_change_db", "median_absolute_rssi_change_db"):
        point_medians[key] = median(row[key] for row in rssi_pairs) if rssi_pairs else None

    by_floor = {}
    for floor in sorted({row["floor"] for row in pairs}):
        rows = [row for row in pairs if row["floor"] == floor]
        collection = {}
        for side in ("d1", "d2"):
            times = [row[f"{side}_start_local"] for row in rows]
            collection[side] = {
                "earliest_session_start_local": min(times),
                "latest_session_start_local": max(times),
                "collectors": sorted({row[f"{side}_collector"] for row in rows}),
                "device_models": sorted({row[f"{side}_device"] for row in rows}),
            }
        by_floor[str(floor)] = {"points": len(rows), "collection": collection}

    return {
        "archives": {
            side: {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for side, path in archives.items()
        },
        "dataset_roles": {"d1": "Original training collection", "d2": "November 11 same-point temporal revisit"},
        "paired_points": len(pairs),
        "orientation": "N",
        "timezones": sorted({row["timezone"] for row in pairs}),
        "scan_config": SCAN_CONFIG,
        "scan_counts": {
            side: {key: sum(row[f"{side}_{key}"] for row in pairs)
                   for key in ("raw_scans", "eligible_scans", "unique_scans")}
            for side in ("d1", "d2")
        },
        "same_collector_points": sum(row["same_collector"] for row in pairs),
        "same_device_model_points": sum(row["same_device_model"] for row in pairs),
        "paired_same_point_exact_fingerprint_overlap": sum(row["exact_shared_fingerprints"] for row in pairs),
        "points_with_shared_bssids": len(rssi_pairs),
        "point_medians": point_medians,
        "by_floor": by_floor,
        "extremes": extremes,
        "focus_points": {row["point_id"]: row for row in pairs if row["point_id"] in FOCUS_POINTS},
        "method": (
            "Use complete static N sessions matched on point ID. Use mobile_reference.prepare_scan to "
            "filter and aggregate each callback. Deduplicate exact BSSID-to-clipped-RSSI fingerprints "
            "within point and date. Count distinct BSSIDs per retained scan and across all retained scans "
            "per date. Jaccard is the count of shared BSSIDs divided by the count in either date. "
            "For each shared BSSID, subtract its D1 median RSSI from its D2 median RSSI, using scans "
            "where it was observed. Summarize signed and absolute shifts separately by their medians."
        ),
        "scope": (
            "D2 here is the November 11 revisit to D1 training point IDs. "
            "The 15-point D2 Static Validation study uses a separate archive. "
            "This is a descriptive same-point comparison. "
            "No model, vocabulary, thresholds, or tuning are fitted or changed by this script. "
            "The comparison combines date, time of day, and collection conditions. AP unions and Jaccard "
            "are sensitive to the number of unique scans retained at each point and date. "
            "Device metadata identifies the handset model, with physical handset identity unverified."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--d1", type=Path, default=ROOT / "data" / "static_train.zip")
    parser.add_argument("--d2", type=Path, default=STAGE / "data" / "temporal_train_20261111.zip")
    parser.add_argument("--output-dir", type=Path, default=STAGE)
    args = parser.parse_args()

    d2 = read_north_sessions(args.d2)
    if not d2:
        raise ValueError("D2 archive has no complete static N sessions")
    d1 = read_north_sessions(args.d1, d2.keys())
    if missing := d2.keys() - d1.keys():
        raise ValueError(f"D1 has no matching static N session for: {', '.join(sorted(missing))}")
    pairs = [compare_pair(point, d1[point], d2[point]) for point in sorted(d2)]
    summary = summarize(pairs, {"d1": args.d1, "d2": args.d2})

    args.output_dir.mkdir(parents=True, exist_ok=True)
    with (args.output_dir / "paired_drift.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(pairs[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(pairs)
    (args.output_dir / "paired_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Compared {len(pairs)} N-facing point pairs in {args.output_dir}")


if __name__ == "__main__":
    main()
