"""Natural-language query layer: question -> safe SELECT -> result table."""
import logging
import os
import re
from decimal import Decimal

import pandas as pd
from sqlalchemy import text

from . import db

log = logging.getLogger("abip.nlq")
MAX_ROWS = 200
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|pragma|copy|call|exec|merge)\b", re.I)

SYSTEM = """You translate business questions into one SQL SELECT query.
Database dialect: {dialect}
Table sales(id, date DATE, month TEXT 'YYYY-MM', product TEXT, region TEXT, qty INTEGER, amount REAL).
amount is revenue in INR. The latest month in the data is '{latest}'; treat "this month" as that month.
Rules: output only the SQL, no markdown, no explanation. Use only the sales table. One statement, SELECT only.
Use the month column for month filters. Add ORDER BY and LIMIT where sensible.
If the question cannot be answered from this table, output exactly: CANNOT_ANSWER"""


class CannotAnswer(Exception):
    pass


def validate(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s or not re.match(r"(?is)^select\b", s) or FORBIDDEN.search(s):
        raise ValueError("Only a single read-only SELECT is allowed.")
    if set(re.findall(r"(?i)\b(?:from|join)\s+([a-z_][a-z0-9_]*)", s)) - {"sales"}:
        raise ValueError("Queries may only use the sales table.")
    return s


def _json(v):
    if v is None or isinstance(v, (int, float, str)):
        return v
    return float(v) if isinstance(v, Decimal) else str(v)


def run(sql: str):
    s = validate(sql)
    with db.engine.connect() as c:
        if db.engine.dialect.name == "postgresql":
            c.execute(text("SET TRANSACTION READ ONLY"))
            c.execute(text("SET LOCAL statement_timeout = 5000"))
        res = c.execute(text(f"SELECT * FROM ({s}) AS q LIMIT {MAX_ROWS}"))
        cols = list(res.keys())
        rows = [[_json(v) for v in r] for r in res.fetchall()]
    return s, cols, rows


def latest_month() -> str:
    with db.engine.connect() as c:
        return c.execute(text("SELECT MAX(month) FROM sales")).scalar() or ""


def llm_sql(question: str, latest: str) -> str:
    import anthropic
    client = anthropic.Anthropic(timeout=20.0)
    msg = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"), max_tokens=400,
        system=SYSTEM.format(dialect=db.engine.dialect.name, latest=latest),
        messages=[{"role": "user", "content": question}])
    out = re.sub(r"^```(?:sql)?|```$", "", msg.content[0].text.strip(), flags=re.M).strip()
    if "CANNOT_ANSWER" in out:
        raise CannotAnswer()
    return out


def rules(question: str, latest: str):
    """Built-in fallback that works without an API key."""
    q = question.lower()
    m = str(pd.Period(latest, "M") - 1) if ("last month" in q or "previous month" in q) else latest
    if "region" in q:
        return (f"Revenue by region, {m}",
                f"SELECT region, ROUND(SUM(amount)) AS revenue FROM sales WHERE month = '{m}' GROUP BY region ORDER BY revenue DESC")
    if any(w in q for w in ("trend", "monthly", "by month", "over time")):
        return ("Revenue by month",
                "SELECT month, ROUND(SUM(amount)) AS revenue FROM sales GROUP BY month ORDER BY month DESC LIMIT 12")
    if any(w in q for w in ("product", "selling", "best", "top")):
        return (f"Top products, {m}",
                f"SELECT product, SUM(qty) AS units, ROUND(SUM(amount)) AS revenue FROM sales WHERE month = '{m}' GROUP BY product ORDER BY units DESC LIMIT 5")
    if "order" in q:
        return (f"Orders, {m}", f"SELECT COUNT(*) AS orders FROM sales WHERE month = '{m}'")
    if any(w in q for w in ("sales", "revenue", "total")):
        return (f"Total sales, {m}", f"SELECT ROUND(SUM(amount)) AS total_sales FROM sales WHERE month = '{m}'")
    return None


HELP = ("I couldn't answer that. Try: top products this month, total sales last month, "
        "revenue by region, or the monthly revenue trend.")


def answer(question: str) -> dict:
    question = question.strip()[:300]
    latest = latest_month()
    if not latest:
        raise ValueError("There is no data yet. Upload a CSV first.")
    candidates, note = [], None
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            candidates.append(("Answer", llm_sql(question, latest), "llm"))
        except CannotAnswer:
            raise ValueError("The sales data can't answer that. Try asking about products, regions, months or revenue.")
        except Exception:
            log.exception("LLM query generation failed")
            note = "The AI service was unavailable, so a built-in query was used."
    r = rules(question, latest)
    if r:
        candidates.append((r[0], r[1], "rules"))
    for title, sql, source in candidates:
        try:
            s, cols, rows = run(sql)
            return {"title": title, "sql": s, "columns": cols, "rows": rows,
                    "source": source, "note": note if source == "rules" else None}
        except Exception:
            log.exception("Query failed (%s)", source)
            note = "The generated query failed, so a built-in query was used."
    raise ValueError(HELP)
