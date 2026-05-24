"""Hidden Markov Model regime detection for crypto markets.

Per academic research (2025-2026 papers):
- 3-state Gaussian HMM on BTC daily log returns
- States classified BULL / NEUTRAL / BEAR by mean return ranking
- Soft state probabilities (Viterbi alternative)
- Used by auto_trader to gate strategy activation per regime

Reference: Preprints 2026 "Markov and Hidden Markov Models for Regime
Detection in Cryptocurrency Markets: Evidence from Bitcoin (2024-2026)"
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn import hmm


MODEL_PATH = Path(__file__).resolve().parent.parent / "data" / "regime_hmm.pkl"
DEFAULT_LOOKBACK_DAYS = 730  # 2 years
DEFAULT_N_STATES = 3


@dataclass
class RegimeSnapshot:
    """Current regime + soft probabilities."""
    timestamp: datetime
    regime: str             # "BULL" / "NEUTRAL" / "BEAR"
    confidence: float       # max probability
    probs: dict             # {label: prob}
    state_means: dict       # {label: historical mean return per state}
    n_observations: int

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat() if hasattr(self.timestamp, "isoformat") else str(self.timestamp),
            "regime": self.regime,
            "confidence": round(self.confidence, 4),
            "probs": {k: round(v, 4) for k, v in self.probs.items()},
            "state_means_pct_daily": {k: round(v * 100, 3) for k, v in self.state_means.items()},
            "n_observations": self.n_observations,
        }


def fit_hmm_on_returns(
    log_returns: np.ndarray,
    n_states: int = DEFAULT_N_STATES,
    n_iter: int = 200,
    random_state: int = 42,
) -> hmm.GaussianHMM:
    """Fit Gaussian HMM on log-return series."""
    model = hmm.GaussianHMM(
        n_components=n_states,
        covariance_type="diag",
        n_iter=n_iter,
        random_state=random_state,
        tol=1e-4,
    )
    model.fit(log_returns.reshape(-1, 1))
    return model


def label_states_by_mean(
    model: hmm.GaussianHMM,
    log_returns: np.ndarray,
) -> tuple[list[str], dict[int, str], dict[str, float]]:
    """Predict states + label them BULL/NEUTRAL/BEAR by mean return.

    Returns (per-obs labels, state-id→label map, label→mean-return map).
    """
    states = model.predict(log_returns.reshape(-1, 1))
    unique_states = sorted(set(states))
    # Compute mean log return per state
    state_means_raw = {
        int(s): float(log_returns[states == s].mean())
        for s in unique_states
    }
    # Sort by mean desc (highest = bull)
    sorted_by_mean = sorted(state_means_raw.items(), key=lambda x: -x[1])
    n_states = len(sorted_by_mean)
    labels_order = ["BULL", "NEUTRAL", "BEAR"][:n_states]
    state_to_label = {}
    label_to_mean = {}
    for (state_id, mean_ret), label in zip(sorted_by_mean, labels_order):
        state_to_label[state_id] = label
        label_to_mean[label] = mean_ret
    obs_labels = [state_to_label[int(s)] for s in states]
    return obs_labels, state_to_label, label_to_mean


def fetch_btc_daily_returns(
    storage,
    exchange: str = "bybit",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Fetch BTC daily close + log returns from DB."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    df = storage._conn.execute(
        "SELECT ts, close FROM ohlcv "
        "WHERE exchange=? AND symbol='BTC/USDT' AND timeframe='1h' AND ts >= ? "
        "ORDER BY ts",
        [exchange, cutoff],
    ).fetchdf()
    if df.empty:
        return pd.DataFrame(), np.array([])
    df["ts"] = pd.to_datetime(df["ts"])
    # Resample 1h → 1d
    daily = df.set_index("ts").resample("1D").last().dropna()
    log_ret = np.log(daily["close"] / daily["close"].shift(1)).dropna()
    daily_with_ret = daily.iloc[1:].copy()
    daily_with_ret["log_ret"] = log_ret.values
    return daily_with_ret, log_ret.values


def get_current_regime(
    storage,
    exchange: str = "bybit",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    use_cached: bool = True,
) -> Optional[RegimeSnapshot]:
    """Fit HMM + classify current BTC regime."""
    daily, log_ret = fetch_btc_daily_returns(storage, exchange, lookback_days)
    if len(log_ret) < 30:
        return None

    # Try cached model first (saves CPU)
    model = None
    if use_cached and MODEL_PATH.exists():
        try:
            with MODEL_PATH.open("rb") as f:
                cached = pickle.load(f)
            # Validate cached model still applicable (right n_states)
            if cached.get("n_states") == DEFAULT_N_STATES and "model" in cached:
                model = cached["model"]
        except Exception:
            model = None

    if model is None:
        model = fit_hmm_on_returns(log_ret, n_states=DEFAULT_N_STATES)
        # Persist
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with MODEL_PATH.open("wb") as f:
                pickle.dump({
                    "n_states": DEFAULT_N_STATES,
                    "model": model,
                    "fitted_at": datetime.now(timezone.utc).isoformat(),
                    "n_observations": len(log_ret),
                }, f)
        except Exception:
            pass

    # Predict + label
    obs_labels, state_to_label, label_to_mean = label_states_by_mean(model, log_ret)
    # Current state probabilities (soft)
    state_probs = model.predict_proba(log_ret.reshape(-1, 1))[-1]
    label_probs = {state_to_label[int(s)]: float(state_probs[s])
                   for s in range(len(state_probs)) if int(s) in state_to_label}
    # Max prob label = current regime
    current_label = max(label_probs.items(), key=lambda x: x[1])[0]
    confidence = label_probs[current_label]

    last_ts = daily.index[-1]
    if hasattr(last_ts, "to_pydatetime"):
        last_ts = last_ts.to_pydatetime()
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)

    return RegimeSnapshot(
        timestamp=last_ts,
        regime=current_label,
        confidence=confidence,
        probs=label_probs,
        state_means=label_to_mean,
        n_observations=len(log_ret),
    )


def regime_history(
    storage,
    exchange: str = "bybit",
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> Optional[pd.DataFrame]:
    """Return DataFrame: [date, close, log_ret, regime] for entire fitted period."""
    daily, log_ret = fetch_btc_daily_returns(storage, exchange, lookback_days)
    if len(log_ret) < 30:
        return None
    model = fit_hmm_on_returns(log_ret, n_states=DEFAULT_N_STATES)
    obs_labels, _, _ = label_states_by_mean(model, log_ret)
    daily = daily.copy()
    daily["regime"] = obs_labels
    return daily.reset_index()


def regime_for_timestamp(
    history_df: pd.DataFrame,
    ts: datetime,
) -> Optional[str]:
    """Look up regime for a specific timestamp from history dataframe."""
    if history_df is None or history_df.empty:
        return None
    if hasattr(ts, "tz_localize"):
        ts = ts.tz_localize(None) if ts.tzinfo else ts
    # Match closest preceding daily bar
    history_df = history_df.copy()
    history_df["ts"] = pd.to_datetime(history_df["ts"])
    if history_df["ts"].dt.tz is not None:
        history_df["ts"] = history_df["ts"].dt.tz_localize(None)
    valid = history_df[history_df["ts"] <= pd.Timestamp(ts)]
    if valid.empty:
        return None
    return str(valid.iloc[-1]["regime"])
