"""Write Victorian public holidays to seeds/vic_public_holidays.csv.

Run once (or when the year range changes). The CSV is committed and loaded
into DuckDB with `dbt seed`.
"""

import csv
from pathlib import Path

import holidays

YEARS = [2024, 2025, 2026]
OUT_PATH = Path(__file__).resolve().parent.parent / "seeds" / "vic_public_holidays.csv"


def main():
    vic_holidays = holidays.AU(subdiv="VIC", years=YEARS)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["holiday_date", "holiday_name"])
        for day, name in sorted(vic_holidays.items()):
            writer.writerow([day.isoformat(), name])

    print(f"wrote {len(vic_holidays)} holidays to {OUT_PATH}")


if __name__ == "__main__":
    main()
