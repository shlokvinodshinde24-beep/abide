# ABIP — Autonomous Business Intelligence Platform

Pipeline: **data source → ETL (pandas) → PostgreSQL → ML analysis (scikit-learn) → natural-language query (Claude) → React-free web dashboard**.

## Structure
- `backend/` FastAPI app: login, summary API, anomaly detection, 6-month forecast, natural-language query, CSV upload
- `frontend/` login page and dashboard (served by the backend)
- `ingest.py` command-line data ingestion
- `render.yaml` one-click Render deployment (web service + free Postgres)

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional; defaults work for a local demo
uvicorn backend.main:app --reload
```
Open http://localhost:8000. Default login is `admin` / `abip@2026`. On first start the app loads 24 months of sample data into SQLite.

Load your own data:
```bash
python ingest.py sales.csv --replace    # columns: date, product, qty, amount, [region]
```
or use **Upload CSV** on the dashboard.

## Deploy free on Render
1. Push this repo to GitHub.
2. Render → **New → Blueprint** → pick the repo. Render reads `render.yaml`.
3. When prompted, set `ADMIN_PASSWORD` (and optionally `ANTHROPIC_API_KEY` for AI-written SQL).
4. After the deploy finishes, your link is `https://abip.onrender.com` (or `abip-xxxx.onrender.com` if the name is taken).

## Notes
- Without `ANTHROPIC_API_KEY`, natural-language questions use built-in rules (top products, total sales, orders, revenue by region, monthly trend).
- Generated SQL is restricted to a single read-only `SELECT` on the `sales` table, with a row limit.
- Single admin account from environment variables. Add a users table before multi-user use.
- Free Render services sleep when idle, and free Postgres has limits and an expiry. Check Render's current free-tier terms.
