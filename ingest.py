"""Command-line data ingestion.

  python ingest.py --seed                 # load 24 months of sample data
  python ingest.py sales.csv              # append a CSV
  python ingest.py sales.csv --replace    # replace existing data

CSV columns: date, product, qty, amount  (region optional)
"""
import argparse
import sys

import pandas as pd

from backend import db

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("csv", nargs="?")
p.add_argument("--seed", action="store_true")
p.add_argument("--replace", action="store_true")
a = p.parse_args()
if not a.csv and not a.seed:
    p.error("give a CSV path or --seed")
db.init()
try:
    print(db.seed() if a.seed else db.load_dataframe(pd.read_csv(a.csv), replace=a.replace))
except ValueError as e:
    sys.exit(f"Error: {e}")
