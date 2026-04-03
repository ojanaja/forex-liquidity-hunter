"""
Quant Walk-Forward Optimizer
============================
Calibrates pair-specific quant settings with rolling walk-forward splits.

What it optimizes:
1. Factor weights (trend, momentum, mean-reversion, volatility penalty)
2. Entry threshold (absolute score gate)

Usage:
    python quant_walkforward.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from statistics import median

import numpy as np
import pandas as pd

try:
    import MetaTrader5 as mt5  # type: ignore[reportMissingImports]
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

import config


@dataclass
class FoldResult:
    symbol: str
    fold_idx: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    threshold: float
    trade_count: int
    mean_pnl: float
    sharpe_like: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _get_override(symbol: str, key: str, default):
    overrides = getattr(config, "QUANT_SYMBOL_OVERRIDES", {}) or {}
    if symbol in overrides and key in overrides[symbol]:
        return overrides[symbol][key]
    return getattr(config, key, default)


def _connect_mt5() -> bool:
    if not MT5_AVAILABLE:
        print("MetaTrader5 package not available.")
        return False

    kwargs = {
        "login": config.MT5_LOGIN,
        "password": config.MT5_PASSWORD,
        "server": config.MT5_SERVER,
    }
    if config.MT5_PATH:
        kwargs["path"] = config.MT5_PATH

    return bool(mt5.initialize(**kwargs))


def _load_bars(symbol: str, timeframe: int, start: datetime, end: datetime) -> pd.DataFrame | None:
    rates = mt5.copy_rates_range(symbol, timeframe, start, end)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def _normalize_csv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize common OHLCV CSV column naming variants."""
    rename_map = {
        "timestamp": "time",
        "datetime": "time",
        "date": "time",
        "Time": "time",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "tick_volume",
    }
    for old, new in rename_map.items():
        if old in df.columns and new not in df.columns:
            df = df.rename(columns={old: new})
    return df


def _load_bars_csv(symbol: str, timeframe_minutes: int, start: datetime, end: datetime) -> pd.DataFrame | None:
    """
    Load OHLCV data from CSV fallback.

    Supported filenames in QUANT_CSV_DATA_DIR:
    - <SYMBOL>_M<tf>.csv
    - <SYMBOL>.csv
    """
    data_dir = Path(getattr(config, "QUANT_CSV_DATA_DIR", "data"))
    candidates = [
        data_dir / f"{symbol}_M{timeframe_minutes}.csv",
        data_dir / f"{symbol}.csv",
    ]

    path = None
    for c in candidates:
        if c.exists():
            path = c
            break
    if path is None:
        return None

    try:
        df = pd.read_csv(path)
    except Exception:
        return None

    df = _normalize_csv_columns(df)
    required = {"time", "open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return None

    df["time"] = pd.to_datetime(df["time"], errors="coerce", utc=False)
    df = df.dropna(subset=["time", "open", "high", "low", "close"]).copy()
    if df.empty:
        return None

    if "tick_volume" not in df.columns:
        df["tick_volume"] = 0.0

    df = df.sort_values("time")
    df = df[(df["time"] >= start) & (df["time"] <= end)]
    if df.empty:
        return None

    return df.reset_index(drop=True)


def _derive_weight_template(df: pd.DataFrame, symbol: str) -> dict:
    ret = df["close"].pct_change().dropna()
    if len(ret) < 50:
        return {
            "trend": float(_get_override(symbol, "QUANT_W_TREND", 0.45)),
            "mom": float(_get_override(symbol, "QUANT_W_MOMENTUM", 0.35)),
            "mr": float(_get_override(symbol, "QUANT_W_MEAN_REVERSION", 0.20)),
            "vol_penalty": float(_get_override(symbol, "QUANT_W_VOL_PENALTY", 0.25)),
        }

    auto1 = float(ret.autocorr(lag=1)) if len(ret) > 10 else 0.0
    vol = float(ret.std())

    trend_w = _clamp(0.45 + (0.20 * auto1), 0.25, 0.65)
    mr_w = _clamp(0.20 - (0.12 * auto1), 0.10, 0.35)
    mom_w = _clamp(1.0 - trend_w - mr_w, 0.15, 0.50)

    # Normalize weights to sum 1.0.
    scale = trend_w + mom_w + mr_w
    trend_w /= scale
    mom_w /= scale
    mr_w /= scale

    # Higher base volatility gets stronger regime penalty.
    vol_penalty = _clamp(0.20 + (vol * 120.0), 0.20, 0.45)

    return {
        "trend": trend_w,
        "mom": mom_w,
        "mr": mr_w,
        "vol_penalty": vol_penalty,
    }


def _build_features(df: pd.DataFrame, symbol: str, weights: dict) -> pd.DataFrame:
    out = df.copy()
    close = out["close"]
    ret = close.pct_change()

    ema_fast = int(_get_override(symbol, "QUANT_EMA_FAST", 20))
    ema_slow = int(_get_override(symbol, "QUANT_EMA_SLOW", 80))
    atr_period = int(_get_override(symbol, "QUANT_ATR_PERIOD", 14))
    mom_short = int(_get_override(symbol, "QUANT_MOMENTUM_SHORT_BARS", 12))
    mom_long = int(_get_override(symbol, "QUANT_MOMENTUM_LONG_BARS", 48))
    z_window = int(_get_override(symbol, "QUANT_ZSCORE_WINDOW", 80))
    mean_window = int(_get_override(symbol, "QUANT_MEAN_WINDOW", 60))
    vol_short = int(_get_override(symbol, "QUANT_VOL_SHORT_WINDOW", 24))
    vol_long = int(_get_override(symbol, "QUANT_VOL_LONG_WINDOW", 96))

    out["ema_fast"] = close.ewm(span=ema_fast, adjust=False).mean()
    out["ema_slow"] = close.ewm(span=ema_slow, adjust=False).mean()

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = tr.rolling(window=atr_period).mean()

    trend_raw = (out["ema_fast"] - out["ema_slow"]) / out["atr"]
    out["trend"] = trend_raw.clip(-3.0, 3.0) / 3.0

    spread = close.pct_change(mom_short) - close.pct_change(mom_long)
    spread_mean = spread.rolling(window=z_window).mean()
    spread_std = spread.rolling(window=z_window).std()
    out["mom"] = ((spread - spread_mean) / spread_std).clip(-3.0, 3.0) / 3.0

    mean = close.rolling(window=mean_window).mean()
    std = close.rolling(window=mean_window).std()
    out["mr"] = (-(close - mean) / std).clip(-3.0, 3.0) / 3.0

    vol_ratio = ret.rolling(window=vol_short).std() / \
        ret.rolling(window=vol_long).std()
    out["vol_penalty"] = (vol_ratio - 1.0).clip(lower=0.0)

    raw_score = (
        (weights["trend"] * out["trend"]) +
        (weights["mom"] * out["mom"]) +
        (weights["mr"] * out["mr"])
    )
    penalty = weights["vol_penalty"] * out["vol_penalty"]

    out["score"] = np.where(
        raw_score > 0,
        np.maximum(0.0, raw_score - penalty),
        np.minimum(0.0, raw_score + penalty),
    )

    max_vol_ratio = float(_get_override(symbol, "QUANT_MAX_VOL_RATIO", 1.15))
    require_alignment = bool(_get_override(
        symbol, "QUANT_REQUIRE_TREND_MOM_ALIGNMENT", True))
    vol_ratio_series = out["vol_penalty"] + 1.0
    out = out[vol_ratio_series <= max_vol_ratio]

    if require_alignment:
        direction = np.sign(out["score"])
        out = out[(direction != 0) & (out["trend"] * direction > 0) &
                  (out["mom"] * direction > 0)]

    return out.dropna().reset_index(drop=True)


def _evaluate_threshold(feature_df: pd.DataFrame, threshold: float, hold_bars: int) -> tuple[int, float, float]:
    if len(feature_df) <= hold_bars + 2:
        return 0, 0.0, 0.0

    rets = []
    cost_bps = 2.0

    for i in range(len(feature_df) - hold_bars):
        s = float(feature_df.iloc[i]["score"])
        if abs(s) < threshold:
            continue

        direction = 1.0 if s > 0 else -1.0
        p0 = float(feature_df.iloc[i]["close"])
        p1 = float(feature_df.iloc[i + hold_bars]["close"])
        if p0 <= 0:
            continue

        gross = direction * ((p1 - p0) / p0)
        net = gross - (cost_bps / 10000.0)
        rets.append(net)

    n = len(rets)
    if n == 0:
        return 0, 0.0, 0.0

    mean = float(np.mean(rets))
    std = float(np.std(rets))
    sharpe_like = 0.0 if std <= 1e-12 else (mean / std) * math.sqrt(n)
    return n, mean, sharpe_like


def _walk_forward_for_symbol(symbol: str, df: pd.DataFrame) -> tuple[list[FoldResult], dict]:
    train_days = int(getattr(config, "WFO_TRAIN_DAYS", 60))
    test_days = int(getattr(config, "WFO_TEST_DAYS", 20))
    hold_bars = int(getattr(config, "WFO_HOLD_BARS", 12))
    min_trades = int(getattr(config, "WFO_MIN_TRADES_PER_FOLD", 10))
    thresholds = list(getattr(config, "WFO_THRESHOLD_GRID",
                      [0.12, 0.16, 0.20, 0.24, 0.28, 0.32]))

    if df.empty:
        return [], {}

    start = df["time"].min().to_pydatetime()
    end = df["time"].max().to_pydatetime()

    fold_results: list[FoldResult] = []
    chosen_thresholds = []
    weight_snapshots = []

    cursor = start + timedelta(days=train_days)
    fold_idx = 0

    while cursor + timedelta(days=test_days) <= end:
        fold_idx += 1
        train_start = cursor - timedelta(days=train_days)
        train_end = cursor
        test_start = cursor
        test_end = cursor + timedelta(days=test_days)

        train_df = df[(df["time"] >= train_start) &
                      (df["time"] < train_end)].copy()
        test_df = df[(df["time"] >= test_start) &
                     (df["time"] < test_end)].copy()

        if len(train_df) < 300 or len(test_df) < 100:
            cursor += timedelta(days=test_days)
            continue

        weights = _derive_weight_template(train_df, symbol)
        weight_snapshots.append(weights)

        feat_train = _build_features(train_df, symbol, weights)
        if feat_train.empty:
            cursor += timedelta(days=test_days)
            continue

        best_thr = None
        best_stat = -1e9
        for thr in thresholds:
            n, mean, stat = _evaluate_threshold(feat_train, thr, hold_bars)
            if n < min_trades:
                continue
            if stat > best_stat:
                best_stat = stat
                best_thr = float(thr)

        if best_thr is None:
            cursor += timedelta(days=test_days)
            continue

        chosen_thresholds.append(best_thr)

        feat_test = _build_features(test_df, symbol, weights)
        n_test, mean_test, stat_test = _evaluate_threshold(
            feat_test, best_thr, hold_bars)

        fold_results.append(
            FoldResult(
                symbol=symbol,
                fold_idx=fold_idx,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                threshold=best_thr,
                trade_count=n_test,
                mean_pnl=mean_test,
                sharpe_like=stat_test,
            )
        )

        cursor += timedelta(days=test_days)

    if not fold_results or not chosen_thresholds or not weight_snapshots:
        return fold_results, {}

    summary = {
        "recommended_threshold": float(median(chosen_thresholds)),
        "avg_test_trades": float(np.mean([f.trade_count for f in fold_results])),
        "avg_test_mean_pnl": float(np.mean([f.mean_pnl for f in fold_results])),
        "avg_test_sharpe_like": float(np.mean([f.sharpe_like for f in fold_results])),
        "weights": {
            "QUANT_W_TREND": float(np.mean([w["trend"] for w in weight_snapshots])),
            "QUANT_W_MOMENTUM": float(np.mean([w["mom"] for w in weight_snapshots])),
            "QUANT_W_MEAN_REVERSION": float(np.mean([w["mr"] for w in weight_snapshots])),
            "QUANT_W_VOL_PENALTY": float(np.mean([w["vol_penalty"] for w in weight_snapshots])),
        },
    }

    return fold_results, summary


def run_walkforward() -> None:
    use_mt5 = _connect_mt5()
    if not use_mt5:
        print("Switching to CSV fallback data source.")

    days_back = int(getattr(config, "WFO_DAYS_BACK", 180))
    timeframe = int(getattr(config, "QUANT_TIMEFRAME_MINUTES", 5))
    mt5_tf = None
    if use_mt5:
        tf_map = {
            1: mt5.TIMEFRAME_M1,
            5: mt5.TIMEFRAME_M5,
            15: mt5.TIMEFRAME_M15,
            30: mt5.TIMEFRAME_M30,
            60: mt5.TIMEFRAME_H1,
        }
        mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M5)

    end = datetime.now()
    start = end - timedelta(days=days_back)

    all_results = {}
    all_folds = []

    print("=" * 72)
    print("QUANT WALK-FORWARD OPTIMIZER")
    print("=" * 72)
    print(
        f"Window: {days_back}d | Train/Test: {config.WFO_TRAIN_DAYS}d/{config.WFO_TEST_DAYS}d")
    print(f"Data Source: {'MT5' if use_mt5 else 'CSV'}")

    for symbol in config.SYMBOLS:
        if use_mt5:
            df = _load_bars(symbol, mt5_tf, start, end)
        else:
            df = _load_bars_csv(symbol, timeframe, start, end)

        if df is None or df.empty:
            print(f"[SKIP] {symbol}: no data")
            continue

        folds, summary = _walk_forward_for_symbol(symbol, df)
        if not summary:
            print(f"[SKIP] {symbol}: insufficient folds")
            continue

        all_folds.extend(folds)
        all_results[symbol] = summary

        print(
            f"[OK] {symbol} | thr={summary['recommended_threshold']:.2f} | "
            f"mean={summary['avg_test_mean_pnl']:+.5f} | "
            f"sh={summary['avg_test_sharpe_like']:+.2f}"
        )

    if use_mt5:
        mt5.shutdown()

    if not all_results:
        print("No symbol generated valid walk-forward result.")
        return

    # Print ready-to-paste config override block.
    print("\nSuggested QUANT_SYMBOL_OVERRIDES block:")
    print("QUANT_SYMBOL_OVERRIDES = {")
    for symbol, rec in all_results.items():
        w = rec["weights"]
        print(f"    \"{symbol}\": {{")
        print(f"        \"QUANT_W_TREND\": {w['QUANT_W_TREND']:.3f},")
        print(f"        \"QUANT_W_MOMENTUM\": {w['QUANT_W_MOMENTUM']:.3f},")
        print(
            f"        \"QUANT_W_MEAN_REVERSION\": {w['QUANT_W_MEAN_REVERSION']:.3f},")
        print(
            f"        \"QUANT_W_VOL_PENALTY\": {w['QUANT_W_VOL_PENALTY']:.3f},")
        print(
            f"        \"QUANT_SCORE_ENTRY_THRESHOLD\": {rec['recommended_threshold']:.2f},")
        print("    },")
    print("}")

    report = {
        "generated_at": datetime.now().isoformat(),
        "settings": {
            "days_back": days_back,
            "train_days": config.WFO_TRAIN_DAYS,
            "test_days": config.WFO_TEST_DAYS,
            "hold_bars": config.WFO_HOLD_BARS,
            "threshold_grid": list(config.WFO_THRESHOLD_GRID),
        },
        "results": all_results,
        "folds": [
            {
                "symbol": f.symbol,
                "fold_idx": f.fold_idx,
                "threshold": f.threshold,
                "trade_count": f.trade_count,
                "mean_pnl": f.mean_pnl,
                "sharpe_like": f.sharpe_like,
                "train_start": f.train_start.isoformat(),
                "train_end": f.train_end.isoformat(),
                "test_start": f.test_start.isoformat(),
                "test_end": f.test_end.isoformat(),
            }
            for f in all_folds
        ],
    }

    out_dir = Path(config.LOG_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(out_dir / "walkforward_quant_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\nSaved report: {out_path}")


if __name__ == "__main__":
    run_walkforward()
