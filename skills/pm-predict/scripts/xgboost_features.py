"""
xgboost_features.py — XGBoost Feature Engineering & Prediction for pm-predict skill

Builds features from market + sentiment data, runs them through a trained
XGBoost model, and returns a calibrated probability estimate.

Training happens offline (see train() function).
Inference happens each pipeline cycle (see predict() function).

NOTE: In Phase 2B (Week 3-4), collect data and train the model.
Until trained, this module raises ModelNotTrainedError so the pipeline
falls back to LLM-only consensus.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODEL_PATH = Path(__file__).parent.parent.parent.parent / "data" / "xgboost_model.pkl"
ENCODER_PATH = Path(__file__).parent.parent.parent.parent / "data" / "category_encoder.pkl"

FEATURE_COLUMNS = [
    "days_to_expiry",
    "volume_24h_log",        # log1p(volume_24h)
    "open_interest_log",     # log1p(open_interest)
    "current_yes_price",
    "sentiment_score",
    "sentiment_confidence",
    "anomaly_flag_count",
    "category_encoded",
]


class ModelNotTrainedError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Feature Engineering
# ---------------------------------------------------------------------------

def build_features(candidate: dict[str, Any]) -> list[float]:
    """Transform raw candidate fields into model features."""
    import math

    category_map = {
        "politics": 0, "finance": 1, "sports": 2,
        "science": 3, "entertainment": 4, "other": 5,
    }
    category = candidate.get("category", "other").lower()
    category_encoded = category_map.get(category, 5)

    sentiment = candidate.get("sentiment", {})

    return [
        float(candidate.get("days_to_expiry", 30)),
        math.log1p(float(candidate.get("volume_24h", 0))),
        math.log1p(float(candidate.get("open_interest", 0))),
        float(candidate.get("current_yes_price", 0.5)),
        float(sentiment.get("score", 0.0)),
        float(sentiment.get("confidence", 0.0)),
        float(len(candidate.get("anomaly_flags", []))),
        float(category_encoded),
    ]


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

def predict(candidate: dict[str, Any]) -> float:
    """
    Return XGBoost probability estimate for the Yes outcome.
    Raises ModelNotTrainedError if model file not found.
    """
    if not MODEL_PATH.exists():
        raise ModelNotTrainedError(
            f"XGBoost model not found at {MODEL_PATH}. "
            "Train the model first using: python scripts/xgboost_features.py --train"
        )

    try:
        import pickle
        import numpy as np

        with open(MODEL_PATH, "rb") as f:
            model = pickle.load(f)

        features = build_features(candidate)
        X = np.array(features).reshape(1, -1)
        prob = float(model.predict_proba(X)[0][1])
        return max(0.01, min(0.99, prob))  # Clamp away from 0/1

    except ModelNotTrainedError:
        raise
    except Exception as e:
        raise RuntimeError(f"XGBoost inference failed: {e}") from e


# ---------------------------------------------------------------------------
# Training (run offline after collecting Phase 1 data)
# ---------------------------------------------------------------------------

def train(training_data_path: str) -> None:
    """
    Train XGBoost on historical resolved markets.

    training_data_path: path to JSONL file where each line is:
    {candidate fields} + {"outcome": 1 or 0}

    Run after 30+ resolved markets are collected in Phase 2B.
    """
    try:
        import pickle
        import numpy as np
        from xgboost import XGBClassifier
        from sklearn.calibration import CalibratedClassifierCV
        from sklearn.model_selection import train_test_split

        records = []
        with open(training_data_path) as f:
            for line in f:
                records.append(json.loads(line))

        if len(records) < 30:
            print(f"Warning: Only {len(records)} training examples. Need 30+ for reliable model.")

        X = np.array([build_features(r) for r in records])
        y = np.array([r["outcome"] for r in records])

        # Stratified split preserves class ratio in both train/val sets
        X_train, X_val, y_train, y_val = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        base_model = XGBClassifier(
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            eval_metric="logloss",
        )
        # Sigmoid (Platt) calibration — more stable than isotonic on small datasets.
        # Isotonic needs 1000+ samples; sigmoid works reliably from ~100 samples.
        model = CalibratedClassifierCV(base_model, cv=3, method="sigmoid")
        model.fit(X_train, y_train)

        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(model, f)

        print(f"Model saved to {MODEL_PATH}")

        # Brier score requires both classes present in validation set
        from sklearn.metrics import brier_score_loss
        if len(set(y_val)) < 2:
            print("Validation Brier Score: N/A (validation set has only one class — fetch more data)")
        else:
            val_probs = model.predict_proba(X_val)[:, 1]
            brier = brier_score_loss(y_val, val_probs)
            print(f"Validation Brier Score: {brier:.4f} (target: <0.25)")

    except ImportError as e:
        print(f"Missing dependency: {e}. Install: pip install xgboost scikit-learn", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if "--train" in sys.argv:
        idx = sys.argv.index("--train")
        data_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "data/training_data.jsonl"
        train(data_path)
        return

    candidate = json.load(sys.stdin)
    try:
        prob = predict(candidate)
        print(json.dumps({"xgboost_prob": prob}))
    except ModelNotTrainedError as e:
        print(json.dumps({"xgboost_prob": None, "error": str(e)}))


if __name__ == "__main__":
    main()
