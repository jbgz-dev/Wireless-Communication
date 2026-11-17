"""Fixed D2 candidates fitted exclusively to cleaned D1 Train fingerprints."""

from collections import defaultdict

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression

from pipeline import MAX_AGE_MS, MISSING, encode_transform


TARGETS = ("floor", "zone")
MIN_AP_POINTS = 2


def _stable_ap_vocabulary(train_df: pd.DataFrame) -> list[str]:
    points_by_bssid = defaultdict(set)
    for row in train_df.itertuples():
        for bssid in row.features:
            points_by_bssid[bssid].add(row.point_id)
    return sorted(
        bssid
        for bssid, point_ids in points_by_bssid.items()
        if len(point_ids) >= MIN_AP_POINTS
    )


def _balanced_scan_weights(train_df: pd.DataFrame, points: pd.DataFrame) -> dict:
    scans_per_point = train_df.groupby("point_id")["point_id"].transform("size")
    weights_by_target = {}
    for target in TARGETS:
        points_per_class = train_df[target].map(points[target].value_counts())
        weights = 1.0 / (scans_per_point * points_per_class)
        weights_by_target[target] = (weights / weights.mean()).to_numpy()
    return weights_by_target


def train_candidates(train_df: pd.DataFrame) -> dict[str, dict]:
    """Train two Wi-Fi-only bundles on the deduplicated output of D1 load_data."""
    points = train_df[["point_id", *TARGETS]].drop_duplicates()
    if points["point_id"].duplicated().any():
        raise ValueError("D1 training points must have consistent floor and zone labels")

    vocab = _stable_ap_vocabulary(train_df)
    if not vocab:
        raise ValueError("No BSSID occurs at two or more D1 training points")
    x_train = encode_transform(train_df["features"].tolist(), vocab)
    weights = _balanced_scan_weights(train_df, points)

    config = {
        "bssids": vocab,
        "missing_rssi": MISSING,
        "clip_rssi": [-100, -20],
        "duplicate_bssid_aggregation": "median",
        "max_age_ms": MAX_AGE_MS,
        "targets": list(TARGETS),
        "score_is_calibrated_probability": False,
        "minimum_d1_points_per_ap": MIN_AP_POINTS,
    }
    algorithms = {
        "stable_ap_balanced_rf": (
            RandomForestClassifier,
            {
                "n_estimators": 300,
                "min_samples_leaf": 2,
                "max_features": "sqrt",
                "random_state": 42,
                "n_jobs": 2,
            },
        ),
        "stable_ap_balanced_logistic": (
            LogisticRegression,
            {"C": 0.01, "solver": "lbfgs", "max_iter": 2000},
        ),
    }
    return {
        name: {
            "config": config,
            "models": {
                target: estimator(**params).fit(
                    x_train, train_df[target], sample_weight=weights[target]
                )
                for target in TARGETS
            },
            "training": {
                "source": "D1 Train",
                "points": len(points),
                "scans": len(train_df),
                "weighting": "equal class mass, equal point mass within class",
            },
        }
        for name, (estimator, params) in algorithms.items()
    }
