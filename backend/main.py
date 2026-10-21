"""ABIP API + static frontend. Run: uvicorn backend.main:app"""
import hmac
import io
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import jwt
import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, ml, nlq

log = logging.getLogger("abip")
SECRET = os.getenv("SECRET_KEY", "dev-secret-change-me")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "abip@2026")
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
MAX_UPLOAD = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI):
    if SECRET == "dev-secret-change-me" or ADMIN_PASSWORD == "abip@2026":
        log.warning("Using default SECRET_KEY / ADMIN_PASSWORD. Set your own before sharing this site.")
    db.init()
    if db.row_count() == 0:
        log.info("Empty database: loading sample data")
        db.seed()
    yield


app = FastAPI(title="ABIP", lifespan=lifespan)
bearer = HTTPBearer(auto_error=False)


def current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    try:
        return jwt.decode(cred.credentials, SECRET, algorithms=["HS256"])["sub"]
    except Exception:
        raise HTTPException(401, "Not signed in.")


class Login(BaseModel):
    username: str
    password: str


class Ask(BaseModel):
    question: str


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/login")
def login(b: Login):
    ok = hmac.compare_digest(b.username.encode(), ADMIN_USER.encode()) & \
         hmac.compare_digest(b.password.encode(), ADMIN_PASSWORD.encode())
    if not ok:
        raise HTTPException(401, "Username or password is incorrect. Check both and try again.")
    token = jwt.encode({"sub": b.username, "exp": int(time.time()) + 12 * 3600}, SECRET, algorithm="HS256")
    return {"token": token, "user": b.username}


@app.get("/api/summary")
def summary(user: str = Depends(current_user)):
    m = ml.monthly()
    if len(m) < 3:
        return {"user": user, "empty": True}
    an = ml.anomalies(m)
    fc = ml.forecast(m, [a["month"] for a in an])
    cur, prev = m.iloc[-1], m.iloc[-2]
    aov, aov_prev = cur.revenue / cur.orders, prev.revenue / prev.orders
    pct = lambda a, b: float((a / b - 1) * 100) if b else 0.0
    with db.engine.connect() as c:
        rng = c.exec_driver_sql("SELECT MIN(date), MAX(date) FROM sales").one()
    return {
        "user": user, "empty": False,
        "kpis": [
            {"label": f"Revenue, {cur.month}", "value": float(cur.revenue), "fmt": "money",
             "delta": pct(cur.revenue, prev.revenue), "ref": f"vs {prev.month}"},
            {"label": "Orders", "value": float(cur.orders), "fmt": "int",
             "delta": pct(cur.orders, prev.orders), "ref": f"vs {prev.month}"},
            {"label": "Average order value", "value": float(aov), "fmt": "money",
             "delta": pct(aov, aov_prev), "ref": f"vs {prev.month}"},
            {"label": f"Forecast, {fc['months'][0]}", "value": fc["values"][0], "fmt": "money",
             "delta": pct(fc["values"][0], cur.revenue), "ref": f"vs {cur.month}"},
        ],
        "months": m["month"].tolist(), "revenue": [float(v) for v in m["revenue"]],
        "forecast": fc, "anomalies": an,
        "meta": {"rows": db.row_count(), "from": str(rng[0]), "to": str(rng[1])},
    }


@app.post("/api/ask")
def ask(b: Ask, user: str = Depends(current_user)):
    if not b.question.strip():
        raise HTTPException(400, "Type a question first.")
    try:
        return nlq.answer(b.question)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/upload")
async def upload(replace: bool = False, file: UploadFile = File(...), user: str = Depends(current_user)):
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, "File is larger than 10 MB.")
    try:
        return db.load_dataframe(pd.read_csv(io.BytesIO(data)), replace=replace)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "Couldn't read that file as a CSV.")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="ui")
