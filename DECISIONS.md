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

## 10. Staging keeps market time (AEST) as the timestamp, not Melbourne local time

**Decision:** Leave timestamps in AEMO market time (UTC+10 all year). Add an `interval_start` column, 5 minutes before AEMO's interval-ending timestamp.

**Alternatives:** Convert to Melbourne local time (AEST in winter, AEDT in summer). Convert to UTC.

**Why:** Market time never changes its clocks, so every day has exactly 24 hours and 288 intervals. Local time has a 23-hour day when daylight saving starts and a 25-hour day when it ends. That would break the unique-timestamp test and make "same hour yesterday" harder to calculate. UTC would also be regular, but it's harder to read and doesn't match AEMO's own data. The catch is that people follow clock time: the evening peak shows up an hour earlier in market time during summer. I handle that with a feature in the curated layer, not by changing the key. `interval_start` makes hourly bucketing a simple truncate, so nobody has to remember the interval-ending rule later.

## 11. Staging uses strict casts and row_number() deduplication, materialised as a view

**Decision:** Convert types with `strptime` and `cast`, which stop the build on a bad value. Deduplicate with `row_number()`, keeping one row per interval. Staging models are views; curated models are tables.

**Alternatives:** `try_cast`, which turns bad values into NULL. `select distinct`. Tables everywhere.

**Why:** A strict cast fails loudly, which matches the rest of the pipeline. `try_cast` would hide problems as NULLs. `distinct` only removes rows that are identical in every column, so if AEMO republished an interval with a corrected value, both rows would survive. `row_number()` guarantees one row per timestamp. The data has no duplicates today, but staging is where you defend against them. A view costs nothing to build and is always current, which suits a light clean-up step. Curated is a table because the model training reads it repeatedly.

## 12. Tests: built-in, one generic custom test, one singular test

**Decision:**
- `not_null` and `unique` on the timestamps.
- A reusable `within_range` generic test on demand (0 to 14,000 MW).
- A singular `assert_no_missing_intervals` test.

**Alternatives:** The `dbt_utils` or `dbt_expectations` packages, which already include range and gap tests. Tight demand bounds.

**Why:** A row count can't prove completeness, because a missing interval and a duplicate cancel out. So uniqueness and gaps are checked separately. The gap test builds every expected 5-minute timestamp and returns any that are missing. I wrote the tests myself rather than adding a package, to learn how dbt tests work: a test is a query that returns bad rows, and zero rows is a pass. On a real team I'd use `dbt_utils`. The demand bounds are wide on purpose. Real demand ranged from 1,225 MW (sunny spring middle-of-the-day with lots of rooftop solar) to 10,784 MW (heatwave), and a 10,000 MW limit would have failed on 36 genuine intervals. The test is meant to catch broken data, like wrong units or garbage values, not real extremes.

## 13. Forecast next-day hourly demand, not next-hour

**Decision:** Predict each hour's demand using only information available at least 24 hours earlier.

**Alternatives:** A next-hour forecast, using the previous hour's demand.

**Why:** A next-hour model mostly learns "same as an hour ago". Its error looks impressive but doesn't say much. Day-ahead forecasting is what the market actually plans around: AEMO and generators forecast tomorrow to schedule supply. It's harder, and a more honest test. The rule applied to every feature is that it must have been known 24 hours before the hour being predicted. That rules out the last hour's demand and same-day prices.

## 14. Curated table: hourly grain, mean demand, lag, rolling and calendar features

**Decision:** `fct_hourly_demand` has one row per market hour:
- Target: the mean of the twelve 5-minute demand values in the hour.
- Lags: 24 hours and 168 hours back.
- Rolling means: 24 hours and 7 days, both ending 24 hours back.
- Calendar features: market hour, Melbourne local hour, day of week, weekend flag, month, public holiday flag.
- `n_intervals`, tested to be exactly 12.

**Alternatives:**
- Daily grain.
- Summing the 5-minute values.
- Including price.
- Counting back by time range rather than rows.
- Dropping the first week of rows inside dbt.

**Why:**
- **Hourly grain:** it keeps the daily shape the model has to learn, while being small (17,520 rows) and less noisy than 5-minute data.
- **Mean, not sum:** demand in MW is a rate, so twelve values added together mean nothing. The hourly mean is the average power and equals the energy used in MWh.
- **Lags:** `lag_168h` (same hour last week) captures the weekly pattern and is also the naive baseline.
- **Rolling means:** they capture the recent level, like a hot week, without seeing the last 24 hours.
- **No price:** tomorrow's price isn't known today, so using it would be leakage.
- **Counting back in rows:** this is only safe because the gap test proves one row is always one hour.
- **Partial windows:** the rolling means are set to null when the window isn't full, so early rows don't get a misleading average of fewer hours.
- **Keeping the first week:** the table keeps every hour as an honest record, and the training code drops rows without enough history.
- **Local hour:** an extra feature because people follow the clock on the wall, so the summer evening peak arrives an hour earlier in market time.

## 15. Public holidays as a dbt seed, generated by the `holidays` package

**Decision:** A small script writes Victorian public holidays to `seeds/vic_public_holidays.csv`. The CSV is committed and loaded with `dbt seed`.

**Alternatives:** Type the dates in by hand. Look up holidays in the Python training code.

**Why:** This is exactly what seeds are for: small, static reference data that belongs in version control. Generating it from a maintained library avoids typos in dates that move every year, like Easter and the AFL Grand Final Friday. Keeping it in dbt means the holiday flag sits in the curated table with the other features, documented and tested, rather than hidden in the model code. One known simplification: holidays are matched on the market date, which can be an hour off from local midnight in summer.

## 16. Time-based train, validation and test split

**Decision:**
- Train: 9 November 2024 to 30 April 2026.
- Validation: May to July 2026.
- Test: August to October 2026.

**Alternatives:** A random split. Cross-validation with random folds.

**Why:** Neighbouring hours are almost identical. A random split puts near-copies of test hours into training, so the score looks much better than it would in real use. That's data leakage. Splitting by time matches how the model would be used: learn from the past, predict the future. Training covers both summers and one winter. The test set is the most recent three months and is scored exactly once, at the end. Caveat: the test covers late winter and spring only, so it doesn't show how the model copes with summer heatwaves. With more time I'd use rolling-origin backtesting: repeat the split at several cut-off dates and average the results.

## 17. Two naive baselines before any model

**Decision:** Score "same hour last week" (`lag_168h`) and "same hour yesterday" (`lag_24h`) on exactly the same periods as the model.

**Alternatives:** Report the model's error on its own.

**Why:** An error of 391 MW means nothing without a reference point. A baseline is the score a one-line rule gets. If a model can't beat it, the extra complexity isn't worth it. It also turns the result into something a client understands: "29% lower error than last week's actuals".

## 18. LightGBM, with early stopping on the validation set

**Decision:** LightGBM regressor with default-ish settings (learning rate 0.05, 31 leaves). It allows up to 2,000 trees, but stops once validation MAE hasn't improved for 100 rounds.

**Alternatives:** scikit-learn's HistGradientBoostingRegressor. Linear regression. A neural network.

**Why:**
- **Gradient boosting** is the standard strong choice for tabular data with mixed features. It handles non-linear effects, like the hour-of-day shape and the summer and winter peaks, without hand-built interaction terms.
- **LightGBM over HistGradientBoosting:** they use the same algorithm (HistGradientBoosting was modelled on LightGBM), but LightGBM is the industry standard, has native MLflow support and gives feature importances directly.
- **Not a neural network:** with 13,000 rows it would be overkill.
- **Early stopping:** the validation set picks the number of trees, so the test set stays untouched. No hyperparameter search, to keep it within the evening.

## 19. MAE and MAPE as metrics

**Decision:** Report MAE (in MW) and MAPE (in percent) on validation and test.

**Alternatives:** RMSE. Peak-hour-only error.

**Why:** MAE is in the units people care about, and it's easy to explain: "on average we're off by about 390 MW". MAPE makes results comparable across regions and seasons. MAPE breaks down when actual values get near zero, but Victorian demand never fell below 1,225 MW, so it's safe here. RMSE punishes large misses more heavily, which matters for peak planning. It would be a sensible addition.

## 20. MLflow tracking with a local SQLite store, model registered

**Decision:** Log every run to `mlflow.db` (SQLite) in an experiment called `vic-day-ahead-demand`:
- parameters: split dates, features, hyperparameters, best iteration
- metrics: validation and test MAE and MAPE
- for LightGBM: the model and feature importances, with the model registered as `vic_day_ahead_demand_lgbm`

Features are converted to float, so the logged input schema can accept missing values later.

**Alternatives:** MLflow's plain file store (`mlruns/` only). Printing results to the console or a spreadsheet.

**Why:** Tracking makes every result reproducible: you can see exactly which data split and settings produced which number, and compare runs side by side in the UI. The model registry needs a database behind it, and SQLite is the zero-setup option. In production this would be a shared tracking server backed by Postgres, with artifacts in S3 or Azure Blob storage.
