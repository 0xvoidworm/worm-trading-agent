"""Pairs cointegration checker via Engle-Granger two-step.

Kenapa cointegration, bukan korelasi? Korelasi tinggi tidak menjamin mean
reversion — dua asset bisa tren sama-sama naik tanpa pernah converge. Cointegration
(ADF test pada residual OLS) menjamin spread stationer = mean reverting.

Referensi: Chan, "Algorithmic Trading: Winning Strategies and Their Rationale" Ch.2
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.regression.linear_model import OLS


@dataclass
class CointegrationResult:
    pair: tuple[str, str]
    hedge_ratio: float        # beta dari OLS y = alpha + beta*x
    adf_pvalue: float         # < 0.05 = cointegrated at 95%
    half_life_bars: float     # mean reversion speed (Ornstein-Uhlenbeck fit)
    current_zscore: float     # z-score spread sekarang
    is_cointegrated: bool
    n_bars: int


def _half_life(spread: pd.Series) -> float:
    """Fit OU process: ds = kappa*(mu - s)dt + sigma*dW → half-life = ln(2)/kappa.

    Via OLS: s_t - s_{t-1} = lambda * s_{t-1} + epsilon → kappa = -lambda.
    """
    s = spread.dropna()
    if len(s) < 20:
        return float("inf")
    delta = s.diff().dropna()
    lagged = s.shift(1).dropna().loc[delta.index]
    if len(lagged) < 20 or lagged.std() == 0:
        return float("inf")
    # OLS tanpa const
    beta = (lagged * delta).sum() / (lagged ** 2).sum()
    if beta >= 0:
        return float("inf")  # not mean reverting
    return float(np.log(2) / -beta)


def engle_granger(
    y: pd.Series,
    x: pd.Series,
    pair_names: tuple[str, str] = ("Y", "X"),
) -> CointegrationResult:
    """Two-step Engle-Granger. y & x must be aligned, log-priced recommended."""
    df = pd.concat([y, x], axis=1).dropna()
    df.columns = ["y", "x"]
    if len(df) < 50:
        return CointegrationResult(pair_names, 0.0, 1.0, float("inf"), 0.0, False, len(df))

    # Step 1: OLS regression y = alpha + beta*x
    X = np.column_stack([np.ones(len(df)), df["x"].values])
    model = OLS(df["y"].values, X).fit()
    alpha, beta = model.params
    spread = df["y"] - (alpha + beta * df["x"])

    # Step 2: ADF test on residual
    adf_stat, pval, *_ = adfuller(spread.values, autolag="AIC")

    hl = _half_life(spread)
    current_z = float((spread.iloc[-1] - spread.mean()) / spread.std()) if spread.std() > 0 else 0.0

    return CointegrationResult(
        pair=pair_names,
        hedge_ratio=float(beta),
        adf_pvalue=float(pval),
        half_life_bars=hl,
        current_zscore=current_z,
        is_cointegrated=pval < 0.05,
        n_bars=len(df),
    )
