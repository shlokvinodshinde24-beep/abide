"""Database layer: engine, schema, CSV loading and sample-data seeding."""
import os

import numpy as np
import pandas as pd
from sqlalchemy import (Column, Date, Float, Integer, MetaData, String, Table,
                        create_engine, delete, func, select)


def _url() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./abip.db")
    for old in ("postgres://", "postgresql://"):
        if url.startswith(old):
            return "postgresql+psycopg2://" + url[len(old):]
    return url


URL = _url()
engine = create_engine(
    URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if URL.startswith("sqlite") else {},
)
metadata = MetaData()
sales = Table(
    "sales", metadata,
    Column("id", Integer, primary_key=True),
    Column("date", Date, nullable=False),
    Column("month", String(7), nullable=False, index=True),  # 'YYYY-MM'
    Column("product", String(120), nullable=False),
    Column("region", String(60), nullable=False, server_default="All"),
    Column("qty", Integer, nullable=False),
    Column("amount", Float, nullable=False),
)
REQUIRED = {"date", "product", "qty", "amount"}


def init() -> None:
    metadata.create_all(engine)


def row_count() -> int:
    with engine.connect() as c:
        return c.execute(select(func.count()).select_from(sales)).scalar_one()


def load_dataframe(df: pd.DataFrame, replace: bool = False) -> dict:
    """Clean a dataframe and load it into the sales table (the ETL step)."""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(
            "Missing column(s): " + ", ".join(sorted(missing))
            + ". Expected: date, product, qty, amount (region is optional)."
        )
    if "region" not in df:
        df["region"] = "All"
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["qty"] = pd.to_numeric(df["qty"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["date", "product", "qty", "amount"])
    if df.empty:
        raise ValueError("No valid rows found. Check the date, qty and amount columns.")
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["date"] = df["date"].dt.date
    df["qty"] = df["qty"].astype(int)
    df["product"] = df["product"].astype(str).str.strip()
    df["region"] = df["region"].fillna("All").astype(str).str.strip()
    df = df[["date", "month", "product", "region", "qty", "amount"]]
    with engine.begin() as c:
        if replace:
            c.execute(delete(sales))
        df.to_sql("sales", c, if_exists="append", index=False, chunksize=1000, method="multi")
    return {"loaded": len(df), "skipped": before - len(df)}


PRODUCTS = {  # name: (unit price in INR, popularity)
    "Wireless Earbuds": (2499, 1.6), "Smart Watch": (4999, 1.2),
    "Laptop Stand": (1299, 1.0), "USB-C Hub": (1799, 0.9),
    "Phone Case": (499, 1.8), "Desk Lamp": (999, 0.6),
}
REGIONS = ["North", "South", "East", "West"]


def seed(months: int = 24) -> dict:
    """Generate 24 months of sample sales (with one planted anomaly) and load them."""
    rng = np.random.default_rng(42)
    end = pd.Timestamp.today().normalize().replace(day=1) - pd.Timedelta(days=1)
    start = end - pd.DateOffset(months=months) + pd.Timedelta(days=1)
    bad_month = (end - pd.DateOffset(months=5)).strftime("%Y-%m")
    rows = []
    for i, d in enumerate(pd.date_range(start, end)):
        factor = (1 + 0.012 * i / 30) * (1 + 0.10 * np.sin(2 * np.pi * d.dayofyear / 365))
        if d.strftime("%Y-%m") == bad_month:
            factor *= 0.62
        for name, (price, pop) in PRODUCTS.items():
            for _ in range(rng.poisson(1.5 * pop * factor)):
                q = int(rng.integers(1, 4))
                rows.append((d.date(), name, REGIONS[int(rng.integers(0, 4))], q,
                             round(q * price * float(rng.uniform(0.95, 1.05)), 2)))
    df = pd.DataFrame(rows, columns=["date", "product", "region", "qty", "amount"])
    return load_dataframe(df, replace=True)
