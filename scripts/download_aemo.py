"""Download AEMO 5-minute price and demand CSVs for one region.

Files are saved unchanged to data/raw/. Months already on disk are skipped,
so the script is safe to run again.
"""

import time
from pathlib import Path

import requests

REGION = "VIC1"
START = (2024, 11)  # (year, month), inclusive
END = (2026, 10)  # (year, month), inclusive
URL = "https://aemo.com.au/aemo/data/nem/priceanddemand/PRICE_AND_DEMAND_{yyyymm}_{region}.csv"
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def month_range(start, end):
    """Yield 'YYYYMM' strings from start to end inclusive."""
    year, month = start
    while (year, month) <= end:
        yield f"{year}{month:02d}"
        month += 1
        if month == 13:
            year, month = year + 1, 1


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for yyyymm in month_range(START, END):
        path = OUT_DIR / f"PRICE_AND_DEMAND_{yyyymm}_{REGION}.csv"
        if path.exists():
            print(f"skip   {path.name} (already downloaded)")
            continue

        url = URL.format(yyyymm=yyyymm, region=REGION)
        response = requests.get(url, headers=HEADERS, timeout=60)
        response.raise_for_status()

        # Guard against an HTML error page being saved as a CSV.
        if not response.content.startswith(b"REGION"):
            raise ValueError(f"{url} did not return the expected CSV")

        path.write_bytes(response.content)
        print(f"saved  {path.name} ({len(response.content):,} bytes)")
        time.sleep(1)  # be polite to AEMO's server


if __name__ == "__main__":
    main()
