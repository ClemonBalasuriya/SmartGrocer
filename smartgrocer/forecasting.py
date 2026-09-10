"""
SmartGrocer - per-product demand forecasting.

Implements the four candidate models from the proposal (Objective 1):
Seasonal Naive, Holt-Winters, SARIMA, SARIMAX (festival-calendar exogenous
variable) - fits all that are available, evaluates each with rolling-origin
cross-validation using nRMSE (the model-selection criterion from the
presentation slide), and automatically picks the best one per product.

Seasonal Naive and Holt-Winters are implemented from scratch with
numpy/pandas only (Holt-Winters follows the exact additive equations in the
presentation), so this module works even without statsmodels installed.
SARIMA/SARIMAX use statsmodels, the standard tool for the job - if it isn't
installed, those two candidates are silently skipped and the pipeline still
runs on whichever models are available (see STATSMODELS_AVAILABLE).

Everything here works on a plain pandas Series (date index, qty values) so
it can be unit-tested without a GUI or a live database.
"""

from __future__ import annotations

import itertools
import sqlite3
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .calendar_sl import is_festival_window

try:
    from statsmodels.tsa.statespace.sarimax import SARIMAX
    STATSMODELS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised on machines without statsmodels
    STATSMODELS_AVAILABLE = False

SEASON_LENGTH = 7  # weekly seasonality on daily data


# --------------------------------------------------------------------------- #
# Data access
# --------------------------------------------------------------------------- #

def daily_sales_series(conn: sqlite3.Connection, product_id: int) -> pd.Series:
    """Daily quantity sold for a product, 0-filled on days with no sale,
    voided invoices excluded."""
    rows = conn.execute(
        """SELECT date(i.datetime) d, SUM(ii.qty) q
           FROM invoice_items ii JOIN invoices i ON i.id = ii.invoice_id
           WHERE ii.product_id=? AND i.voided=0
           GROUP BY date(i.datetime) ORDER BY d""",
        (product_id,),
    ).fetchall()
    if not rows:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([r["d"] for r in rows])
    s = pd.Series([r["q"] for r in rows], index=idx)
    full_idx = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full_idx, fill_value=0.0)


def build_festival_exog(date_index: pd.DatetimeIndex) -> np.ndarray:
    return np.array([[is_festival_window(d.date())] for d in date_index], dtype=float)


# --------------------------------------------------------------------------- #
# Candidate models - each returns an ndarray of length `horizon`, or None on failure
# --------------------------------------------------------------------------- #

def forecast_seasonal_naive(train: pd.Series, horizon: int, season: int = SEASON_LENGTH) -> np.ndarray | None:
    y = train.values
    if len(y) < season:
        return None
    return np.array([y[len(y) - season + (h % season)] for h in range(horizon)], dtype=float)


def _fit_holt_winters(y: np.ndarray, m: int, alpha: float, beta: float, gamma: float):
    n = len(y)
    level = [float(np.mean(y[:m]))]
    trend = [float((np.mean(y[m:2 * m]) - np.mean(y[:m])) / m)]
    seasonal = [float(y[i] - level[0]) for i in range(m)]
    fitted = np.full(n, np.nan)
    for t in range(m, n):
        lvl_prev, tr_prev, season_prev = level[-1], trend[-1], seasonal[t - m]
        fitted[t] = lvl_prev + tr_prev + season_prev
        lvl = alpha * (y[t] - season_prev) + (1 - alpha) * (lvl_prev + tr_prev)
        tr = beta * (lvl - lvl_prev) + (1 - beta) * tr_prev
        season = gamma * (y[t] - lvl) + (1 - gamma) * season_prev
        level.append(lvl)
        trend.append(tr)
        seasonal.append(season)
    return level, trend, seasonal, fitted


def forecast_holt_winters(train: pd.Series, horizon: int, season: int = SEASON_LENGTH) -> np.ndarray | None:
    y = train.values.astype(float)
    m = season
    if len(y) < 2 * m + 1:
        return None
    best = None  # (sse, level, trend, seasonal)
    grid = itertools.product([0.1, 0.3, 0.5], [0.05, 0.15, 0.3], [0.1, 0.3, 0.5])
    for alpha, beta, gamma in grid:
        try:
            level, trend, seasonal, fitted = _fit_holt_winters(y, m, alpha, beta, gamma)
        except Exception:
            continue
        mask = ~np.isnan(fitted)
        if not mask.any():
            continue
        sse = float(np.sum((y[mask] - fitted[mask]) ** 2))
        if best is None or sse < best[0]:
            best = (sse, level, trend, seasonal)
    if best is None:
        return None
    _, level, trend, seasonal = best
    forecasts = []
    for h in range(1, horizon + 1):
        season_idx = seasonal[len(seasonal) - m + ((h - 1) % m)]
        forecasts.append(max(0.0, level[-1] + h * trend[-1] + season_idx))
    return np.array(forecasts, dtype=float)


def forecast_sarima(train: pd.Series, horizon: int,
                     order=(1, 1, 1), seasonal_order=(0, 1, 1, SEASON_LENGTH)) -> np.ndarray | None:
    if not STATSMODELS_AVAILABLE or len(train) < 2 * SEASON_LENGTH + 5:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(train.values, order=order, seasonal_order=seasonal_order,
                             enforce_stationarity=False, enforce_invertibility=False)
            fit = model.fit(disp=False)
            pred = fit.get_forecast(steps=horizon).predicted_mean
        return np.clip(np.asarray(pred, dtype=float), 0, None)
    except Exception:
        return None


def forecast_sarimax(train: pd.Series, horizon: int, exog_train: np.ndarray, exog_future: np.ndarray,
                      order=(1, 1, 1), seasonal_order=(0, 1, 1, SEASON_LENGTH)) -> np.ndarray | None:
    if not STATSMODELS_AVAILABLE or len(train) < 2 * SEASON_LENGTH + 5:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = SARIMAX(train.values, exog=exog_train, order=order, seasonal_order=seasonal_order,
                             enforce_stationarity=False, enforce_invertibility=False)
            fit = model.fit(disp=False)
            pred = fit.get_forecast(steps=horizon, exog=exog_future).predicted_mean
        return np.clip(np.asarray(pred, dtype=float), 0, None)
    except Exception:
        return None


MODEL_REGISTRY = {
    "Seasonal Naive": lambda train, horizon, exog_f, exog_t: forecast_seasonal_naive(train, horizon),
    "Holt-Winters": lambda train, horizon, exog_f, exog_t: forecast_holt_winters(train, horizon),
    "SARIMA": lambda train, horizon, exog_f, exog_t: forecast_sarima(train, horizon),
    "SARIMAX": lambda train, horizon, exog_f, exog_t: (
        forecast_sarimax(train, horizon, exog_f, exog_t) if exog_f is not None else None
    ),
}


# --------------------------------------------------------------------------- #
# Evaluation & automatic model selection
# --------------------------------------------------------------------------- #

def nrmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    rmse = float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
    mean_y = float(np.mean(y_true))
    return rmse / mean_y if mean_y > 1e-9 else rmse


def rolling_origin_cv(series: pd.Series, horizon: int = 7, n_folds: int = 3,
                       min_train: int = 60) -> dict[str, float]:
    """Average nRMSE per model across n_folds walk-forward folds.
    A model missing from the result was unavailable/failed on at least one fold."""
    n = len(series)
    origins = [n - horizon * k for k in range(n_folds, 0, -1)]
    origins = [o for o in origins if o >= min_train]
    if not origins:
        return {}

    scores: dict[str, list[float]] = {name: [] for name in MODEL_REGISTRY}
    for origin in origins:
        train = series.iloc[:origin]
        test = series.iloc[origin:origin + horizon]
        if len(test) < horizon:
            continue
        exog_train = build_festival_exog(train.index)
        exog_test = build_festival_exog(test.index)
        for name, fn in MODEL_REGISTRY.items():
            pred = fn(train, horizon, exog_train, exog_test)
            if pred is None or len(pred) != horizon:
                continue
            scores[name].append(nrmse(test.values, pred))

    return {name: float(np.mean(vals)) for name, vals in scores.items()
            if len(vals) == len(origins) and len(vals) > 0}


def select_best_model(conn: sqlite3.Connection, product_id: int, horizon: int = 7,
                       n_folds: int = 3) -> dict:
    """Returns {model_scores, best_model, forecast (np.ndarray), series, anomalies}."""
    series = daily_sales_series(conn, product_id)
    result = {"model_scores": {}, "best_model": None, "forecast": None,
              "series": series, "anomalies": []}
    if len(series) < 30:
        return result  # not enough history yet

    result["anomalies"] = detect_anomalies(series)
    clean_series = series.copy()
    for d in result["anomalies"]:
        clean_series.loc[d] = clean_series.rolling(7, min_periods=1).median().loc[d]

    scores = rolling_origin_cv(clean_series, horizon=horizon, n_folds=n_folds)
    result["model_scores"] = scores
    if not scores:
        # fall back to the one model that always works
        pred = forecast_seasonal_naive(clean_series, horizon)
        result["best_model"] = "Seasonal Naive"
        result["forecast"] = pred
        return result

    best_model = min(scores, key=scores.get)
    exog_train = build_festival_exog(clean_series.index)
    future_dates = pd.date_range(clean_series.index[-1] + timedelta(days=1), periods=horizon, freq="D")
    exog_future = build_festival_exog(future_dates)
    pred = MODEL_REGISTRY[best_model](clean_series, horizon, exog_train, exog_future)
    if pred is None:  # extremely unlikely re-fit failure - fall back
        best_model = "Seasonal Naive"
        pred = forecast_seasonal_naive(clean_series, horizon)

    result["best_model"] = best_model
    result["forecast"] = pred
    result["future_dates"] = future_dates
    return result


def detect_anomalies(series: pd.Series, window: int = 14, z_thresh: float = 2.0) -> list:
    """Flag days where sales deviate more than z_thresh standard deviations from
    a trailing rolling mean (proposal: "two-standard-deviation anomaly detector
    ... flag external shocks and temporarily pause reorder recommendations").
    Returns a list of pandas Timestamps."""
    if len(series) < window + 1:
        return []
    roll_mean = series.rolling(window, min_periods=window).mean().shift(1)
    roll_std = series.rolling(window, min_periods=window).std(ddof=0).shift(1)
    z = (series - roll_mean) / roll_std.replace(0, np.nan)
    flagged = z[(z.abs() > z_thresh)].index
    return list(flagged)


# --------------------------------------------------------------------------- #
# Purchase-list generation (Expected Outcomes: "demand-based purchase list")
# --------------------------------------------------------------------------- #

def generate_purchase_list(conn: sqlite3.Connection, horizon_days: int = 7) -> list[dict]:
    from .pos import get_stock_on_hand  # local import avoids a circular import at module load

    out = []
    products = conn.execute("SELECT * FROM products WHERE active=1").fetchall()
    for p in products:
        result = select_best_model(conn, p["id"], horizon=horizon_days)
        forecast = result["forecast"]
        forecast_demand = float(np.sum(forecast)) if forecast is not None else None
        on_hand = get_stock_on_hand(conn, p["id"])
        paused = len(result["anomalies"]) > 0 and result["anomalies"][-1] >= (
            result["series"].index[-1] - pd.Timedelta(days=3)
        ) if len(result.get("series", [])) else False

        if forecast_demand is None:
            continue
        safety_stock = max(p["reorder_level"] * 0.3, forecast_demand * 0.15)
        suggested_qty = max(0.0, forecast_demand + safety_stock - on_hand)
        if suggested_qty > 0.5 or on_hand < p["reorder_level"]:
            out.append({
                "product_id": p["id"], "code": p["code"], "name": p["name_en"],
                "on_hand": round(on_hand, 1), "forecast_demand_next_period": round(forecast_demand, 1),
                "suggested_reorder_qty": round(suggested_qty, 1),
                "best_model": result["best_model"], "recent_anomaly_pause": paused,
            })
    out.sort(key=lambda r: -r["suggested_reorder_qty"])
    return out
