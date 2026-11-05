# Decision log

Each entry records what I decided, what else I considered, and why.

## 1. Data source: AEMO aggregated price and demand, Victoria (VIC1)

**Decision:** Use AEMO's public monthly price and demand CSVs for the VIC1 region.

**Alternatives:** Kaggle energy datasets, synthetic data.

**Why:** It's the official source for the National Electricity Market, it's free, and it's the kind of infrastructure data WSP works with. Kaggle copies are often stale or undocumented. Synthetic data would make the results meaningless.

**Things I found when checking the data:**
- The data is at 5-minute intervals, not 30-minute. The NEM moved to 5-minute settlement in October 2021. That's about 8,600 rows a month.
- Each timestamp marks the **end** of its interval. A row stamped `00:05` covers 00:00 to 00:05. So when I aggregate to hourly, I shift each timestamp back 5 minutes before truncating to the hour. Otherwise the `01:00` row would land in the wrong hour.
- Timestamps are in market time (AEST, UTC+10, no daylight saving).

## 2. Time range: 24 months, November 2024 to October 2026

**Decision:** 24 full months, stopping at the last complete month.

**Alternatives:** 12 months, or as many years as available.

**Why:** Demand is seasonal (winter heating, summer cooling), so I want every season at least twice: once to train on and once to test on. Going much further back adds data from a different grid, with less rooftop solar and different consumption patterns, which could teach the model things that are no longer true. I left out the current month because it's incomplete.

## 3. Extraction is a separate Python script, not part of dbt

**Decision:** A plain Python script downloads the CSVs to `data/raw/`. dbt only reads local files.

**Alternatives:** Have DuckDB read the CSVs straight from the AEMO URLs inside a dbt model, or load them as dbt seeds.

**Why:** dbt is the "T" in ELT. It transforms data that has already been loaded. If dbt read from URLs, every `dbt run` would re-download everything, and my transforms would break whenever AEMO's site is slow or down. Seeds are meant for small, static reference tables (like a holiday list), not raw source data. In production this extract step would be an Airflow task or a managed tool such as Fivetran.

## 4. Raw files are kept byte for byte and not committed to git

**Decision:** Save the files exactly as downloaded. Exclude `data/` from git.

**Alternatives:** Clean the CSVs while downloading. Commit the data.

**Why:** Keeping an untouched raw copy means I can always rebuild every later layer from the original, and audit a number back to its source. Cleaning belongs in the staging layer, where it's visible and tested. The data is large and anyone can download it again with the script, so it doesn't belong in git.

## 5. The download script is idempotent and fails loudly

**Decision:** Skip months already on disk. Stop with an error on any bad response.

**Alternatives:** Re-download everything every time. Log errors and carry on.

**Why:** Idempotent means running it twice gives the same result as running it once. That makes it safe to re-run after a failure, and it's how pipeline tasks should behave (Airflow retries rely on it). Failing loudly (HTTP errors, or a response that doesn't start with the CSV header) means a missing or broken month is noticed straight away, instead of turning up later as a gap in the model.

## 6. Python 3.12, not the newest 3.14

**Decision:** Build the virtual environment with Python 3.12.

**Alternatives:** Python 3.14 (my default install).

**Why:** dbt and some of its dependencies don't support 3.14 yet. 3.12 is mature and supported by everything in this stack. In general I'd pick the newest version the whole toolchain supports, not the newest version available.

## 7. dbt Core v1, not dbt v2

**Decision:** Use dbt Core 1.x with the `dbt-duckdb` adapter.

**Alternatives:** dbt v2, which is now the default for new installations.

**Why:** The DuckDB adapter, most documentation and tutorials, and most existing team projects are on v1. The core ideas (models, `ref`, sources, tests, materialisations) carry over, so learning on v1 isn't wasted. On a real team I'd match whatever version the existing projects use.

## 8. dbt project at the repo root, with profiles.yml committed

**Decision:** `dbt_project.yml` and `models/` sit at the repo root. `profiles.yml` is in the repo, not in `~/.dbt/`.

**Alternatives:** A `dbt/` subfolder. Keeping `profiles.yml` in the home directory, which is dbt's default. Generating the project with `dbt init`.

**Why:** With one dbt project in a small repo, the root is simplest: commands run without `--project-dir`. `profiles.yml` normally stays out of the repo because it holds passwords. Mine only points at a local DuckDB file, so committing it means anyone can clone the repo and run it straight away. For Redshift or BigQuery I'd keep it outside the repo and read credentials from environment variables. I wrote the config by hand rather than using `dbt init`, so there are no example files to delete and I know what every line does.

## 9. The raw layer is a dbt source that reads the CSVs directly, all columns as text

**Decision:** Declare the CSV files as a dbt source. DuckDB reads them in place with `read_csv(..., all_varchar = true, filename = true)`.

**Alternatives:** A Python script that loads the CSVs into a `raw` table in DuckDB first. dbt seeds.

**Why:** A source is dbt's name for data it didn't create. Declaring it means models refer to `source('aemo', 'price_and_demand')` instead of a file path, and dbt can draw the lineage from raw files to the final table. DuckDB can query CSVs directly, so a separate load step would add work without adding anything. In a warehouse, the load step would be Fivetran or an Airflow task, and the source would point at the table it loaded. Reading every column as text keeps the raw layer truly as-is: no type guessing, and every conversion happens in staging, where it's visible and tested. The `filename` column lets me trace any row back to its file.

**Check:** 210,240 rows, which is exactly 730 days × 288 five-minute intervals, across 24 files.
