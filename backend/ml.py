"""Intelligence layer: monthly aggregation, anomaly detection, forecasting."""
import numpy as np
import pandas as pd
from sqlalchemy import text
from sklearn.ensemble import IsolationForest

from . import db


def monthly() -> pd.DataFrame:
    q = text("SELECT month, SUM(amount) AS revenue, COUNT(*) AS orders, SUM(qty) AS units "
             "FROM sales GROUP BY month ORDER BY month")
    with db.engine.connect() as c:
        df = pd.read_sql(q, c)
    df["revenue"] = df["revenue"].astype(float)
    return df


def anomalies(m: pd.DataFrame) -> list[dict]:
    """Flag months whose revenue deviates from the linear trend (Isolation Forest on residuals)."""
    if len(m) < 8:
        return []
    y = m["revenue"].to_numpy()
    trend = np.polyval(np.polyfit(np.arange(len(y)), y, 1), np.arange(len(y)))
    resid = (y - trend) / trend
    flagged = IsolationForest(contamination=0.08, random_state=0).fit_predict(resid.reshape(-1, 1)) == -1
    out = []
    for i in np.where(flagged & (np.abs(resid) > 0.20))[0]:
        out.append({"month": m["month"].iloc[i], "actual": float(y[i]),
                    "expected": float(trend[i]), "pct": float(resid[i] * 100)})
    return out


def forecast(m: pd.DataFrame, skip: list[str], horizon: int = 6) -> dict:
    """Damped linear trend on the last 6 normal months, with an 80% band."""
    clean = m[~m["month"].isin(skip)].tail(6)
    y = clean["revenue"].to_numpy()
    if len(y) < 3:
        y = m["revenue"].to_numpy()
    x = np.arange(len(y))
    b, a = np.polyfit(x, y, 1)
    sd = float(np.std(y - (a + b * x))) or float(y.mean()) * 0.05
    level, cum = a + b * (len(y) - 1), 0.0
    vals, lo, hi = [], [], []
    for k in range(1, horizon + 1):
        cum += 0.85 ** k
        v = level + b * cum
        w = 1.28 * sd * np.sqrt(k)
        vals.append(float(v)); lo.append(float(v - w)); hi.append(float(v + w))
    last = pd.Period(m["month"].iloc[-1], "M")
    return {"months": [str(last + k) for k in range(1, horizon + 1)],
            "values": vals, "lower": lo, "upper": hi}
