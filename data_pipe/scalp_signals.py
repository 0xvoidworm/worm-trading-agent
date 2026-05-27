"""Rule-based scalp signal engine (P2) — sinyal dari 1m OHLCV.

Lane: fast rule-based, hold 1-5 menit. BUKAN LLM-in-loop, BUKAN HFT.
Sumber data: data/scalp_1m.duckdb (BTC/ETH perp 1m). Lihat docs/futures/SCALPING_SCOPE.md.

═══ PRINSIP NO-LOOKAHEAD (load-bearing, jangan dilanggar) ═══
Sinyal di-DETEKSI pas CLOSE bar `t`, cuma boleh pakai data ≤ t.
ENTRY di OPEN bar `t+1` (lo lihat close, baru bisa act bar berikut).
Semua feature (atr, sigma, vol_ma, z) pakai rolling yang berakhir di t.
entry_price = open.shift(-1). Bar terakhir gak punya entry → di-drop.
Melanggar ini = backtest bohong (hantu +67% lahir dari sini).

═══ SCHEMA OUTPUT (dikonsumsi P3 backtest) ═══
Tiap famili return DataFrame sparse (1 row per sinyal) kolom SIGNAL_COLUMNS:
  ts          : TIMESTAMP bar deteksi (close bar t)
  symbol      : str
  family      : MOMENTUM_BURST | MEAN_REVERSION | VOL_BREAKOUT
  direction   : LONG | SHORT
  entry_price : open bar t+1 (eksekusi realistis)
  sl, tp      : harga absolut, ATR-based, R:R fix
  atr         : ATR @ deteksi (buat sizing/diagnostics)
  score       : kekuatan sinyal (buat ranking/threshold P3)
  thesis      : tag + snapshot param

═══ COST HURDLE (inget) ═══
~10bp BEP/scalp (taker 8bp RT + spread). Sinyal mesti nangkep gerak > itu net.
Sanity peek di sini ABAIKAN cost + intrabar SL/TP + in-sample → smell test, BUKAN gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "scalp_1m.duckdb"

SIGNAL_COLUMNS = [
    "ts", "symbol", "family", "direction",
    "entry_price", "sl", "tp", "atr", "score", "thesis",
]

FAMILIES = ("MOMENTUM_BURST", "MEAN_REVERSION", "VOL_BREAKOUT")


# ─── params per famili (default start; P3 bakal sweep) ──────────────────────

@dataclass
class MomentumBurstParams:
    vol_window: int = 30        # window σ return (bar)
    atr_window: int = 14        # window ATR
    volma_window: int = 20      # window volume MA
    k_sigma: float = 3.0        # ambang thrust dalam σ return (tinggi = jarang & kuat)
    vol_mult: float = 1.5       # konfirmasi volume: volume > vol_mult × MA
    sl_atr: float = 1.0         # jarak SL dalam ATR
    rr: float = 1.5             # R:R → TP = entry ± rr × (jarak SL)


@dataclass
class MeanReversionParams:
    z_window: int = 30
    atr_window: int = 14
    z_entry: float = 2.5        # |z| > z_entry → fade
    sl_atr: float = 1.0
    tp_to_mean: bool = True     # TP = rolling mean (revert penuh); else rr-based


@dataclass
class VolBreakoutParams:
    breakout_window: int = 20   # break high/low N-bar
    atr_window: int = 14
    squeeze_pct: float = 0.30   # gate: ATR di bawah persentil ini (low-vol squeeze) dulu
    squeeze_lookback: int = 120
    sl_atr: float = 1.0
    rr: float = 1.5


# ─── data + features ────────────────────────────────────────────────────────

def load_klines(
    symbol: str, db_path: Path = DEFAULT_DB,
    start: Optional[str] = None, end: Optional[str] = None,
) -> pd.DataFrame:
    """Load 1m OHLCV satu symbol dari scalp DB, urut by ts. read_only (aman, gak nge-lock)."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        q = "SELECT ts, open, high, low, close, volume FROM ohlcv WHERE symbol = ?"
        args: list = [symbol]
        if start:
            q += " AND ts >= ?"; args.append(start)
        if end:
            q += " AND ts < ?"; args.append(end)
        q += " ORDER BY ts"
        df = con.execute(q, args).df()
    finally:
        con.close()
    return df.reset_index(drop=True)


def add_features(df: pd.DataFrame, atr_window: int = 14,
                 vol_window: int = 30, volma_window: int = 20) -> pd.DataFrame:
    """Tambah feature no-lookahead (semua rolling berakhir di bar t).

    ret, sigma (σ return), atr, vol_ma, plus open_next (= open t+1, entry realistis).
    """
    df = df.copy()
    df["ret"] = df["close"].pct_change()
    df["sigma"] = df["ret"].rolling(vol_window).std()

    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(atr_window).mean()

    df["vol_ma"] = df["volume"].rolling(volma_window).mean()
    # entry realistis: open bar BERIKUTNYA (no-lookahead). bar terakhir → NaN → drop di famili.
    df["open_next"] = df["open"].shift(-1)
    return df


def _empty_signals() -> pd.DataFrame:
    return pd.DataFrame(columns=SIGNAL_COLUMNS)


# ─── FAMILI 1: MOMENTUM BURST (diimplementasi penuh — template) ──────────────

def momentum_burst(df: pd.DataFrame, symbol: str,
                   p: MomentumBurstParams = MomentumBurstParams()) -> pd.DataFrame:
    """Thrust continuation: bar-return spike > k·σ + konfirmasi volume → ikut arah.

    Deteksi @ close t (ret_t, σ_t, vol_ma_t semua ≤ t). Entry @ open t+1.
    LONG kalau thrust naik, SHORT kalau thrust turun.
    SL = entry ∓ sl_atr·ATR ; TP = entry ± rr·(jarak SL).
    """
    d = add_features(df, p.atr_window, p.vol_window, p.volma_window)
    cond_vol = d["volume"] > p.vol_mult * d["vol_ma"]
    up = (d["ret"] > p.k_sigma * d["sigma"]) & cond_vol
    dn = (d["ret"] < -p.k_sigma * d["sigma"]) & cond_vol
    # valid butuh feature + entry (open_next) tersedia
    valid = d["atr"].notna() & d["sigma"].notna() & d["vol_ma"].notna() & d["open_next"].notna()
    up &= valid; dn &= valid

    sig = d[up | dn].copy()
    if sig.empty:
        return _empty_signals()
    sig["direction"] = np.where(up[up | dn], "LONG", "SHORT")
    sig["entry_price"] = sig["open_next"]
    long_mask = sig["direction"] == "LONG"
    sl_dist = p.sl_atr * sig["atr"]
    sig["sl"] = np.where(long_mask, sig["entry_price"] - sl_dist, sig["entry_price"] + sl_dist)
    sig["tp"] = np.where(long_mask, sig["entry_price"] + p.rr * sl_dist,
                         sig["entry_price"] - p.rr * sl_dist)
    sig["score"] = (sig["ret"].abs() / sig["sigma"]).round(2)  # kekuatan thrust dalam σ
    sig["symbol"] = symbol
    sig["family"] = "MOMENTUM_BURST"
    sig["thesis"] = ("burst k=" + str(p.k_sigma) + " volx=" + str(p.vol_mult)
                     + " rr=" + str(p.rr))
    return sig[SIGNAL_COLUMNS].reset_index(drop=True)


# ─── FAMILI 2 & 3: STUB (desain — diisi penuh next session) ──────────────────

def mean_reversion(df: pd.DataFrame, symbol: str,
                   p: MeanReversionParams = MeanReversionParams()) -> pd.DataFrame:
    """[STUB P2-next] Fade ekstrem: z = (close − rollmean)/rollstd @ close t.
    |z| > z_entry → fade (z>0 SHORT, z<0 LONG). Entry open t+1.
    SL = entry ∓ sl_atr·ATR ; TP = rolling mean (revert) atau rr-based.
    Risiko: mean-rev 1m crypto = noise microstructure; gampang ketipu trend (z stretch terus).
    Butuh: regime/trend filter? half-life check? → eksplor di build.
    """
    raise NotImplementedError("mean_reversion: diisi P2 build session berikutnya")


def vol_breakout(df: pd.DataFrame, symbol: str,
                 p: VolBreakoutParams = VolBreakoutParams()) -> pd.DataFrame:
    """[STUB P2-next] Range expansion: close break max(high,N)/min(low,N) SETELAH squeeze
    (ATR < persentil squeeze_pct atas lookback). Break atas → LONG, bawah → SHORT.
    Entry open t+1. SL = sisi seberang level / ATR ; TP = rr-based.
    Risiko: false breakout (fakeout) = musuh utama; squeeze-gate buat saring.
    """
    raise NotImplementedError("vol_breakout: diisi P2 build session berikutnya")


# ─── sanity peek (SMELL TEST, BUKAN backtest gate P3) ────────────────────────

def forward_return_peek(df: pd.DataFrame, signals: pd.DataFrame,
                        hold_bars: int = 5) -> dict:
    """Distribusi forward-return per sinyal di hold_bars ke depan (dari entry open t+1).

    ⚠️ ABAIKAN cost, intrabar SL/TP, in-sample. INI SMELL TEST — bukan edge.
    Tujuan: cek sinyal gak degenerate + apakah magnitude move > cost hurdle (~10bp)
    sebelum capek bikin backtest penuh. fwd_ret untuk LONG positif = sinyal searah.
    """
    if signals.empty:
        return {"n": 0}
    # map ts → index buat ambil harga hold_bars setelah ENTRY (entry = bar t+1)
    idx_of_ts = pd.Series(df.index.values, index=df["ts"].values)
    out = []
    close = df["close"].values
    for _, s in signals.iterrows():
        t = idx_of_ts.get(s["ts"])
        if t is None:
            continue
        entry_i = t + 1                  # entry bar (open t+1)
        exit_i = entry_i + hold_bars
        if exit_i >= len(df):
            continue
        entry = s["entry_price"]
        fwd = (close[exit_i] - entry) / entry
        if s["direction"] == "SHORT":
            fwd = -fwd                   # short untung kalau harga turun
        out.append(fwd)
    if not out:
        return {"n": 0}
    arr = np.array(out) * 10_000.0       # ke bps
    return {
        "n": len(arr),
        "mean_bps": round(float(arr.mean()), 2),
        "median_bps": round(float(np.median(arr)), 2),
        "win_rate": round(float((arr > 0).mean()), 3),
        "mean_minus_cost_bps": round(float(arr.mean()) - 10.0, 2),  # vs ~10bp hurdle
        "hold_bars": hold_bars,
    }
