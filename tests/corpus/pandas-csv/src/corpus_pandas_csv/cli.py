"""Sums a CSV file by group with pandas."""

import sys

import pandas as pd


def main() -> None:
    frame = pd.read_csv(sys.argv[1], parse_dates=["day"])
    totals = frame.groupby("region", sort=True)["amount"].agg(["sum", "mean", "count"])
    print(totals.round(2).to_string())
    print(frame["day"].dt.month.value_counts().sort_index().to_string())
