"""Train and compare day-ahead demand forecasts, logging each run to MLflow.

Reads the curated table built by dbt, splits it by time, scores two naive
baselines and a LightGBM model on the same validation and test periods.

Run `dbt build` first. View results with:
    mlflow ui --backend-store-uri sqlite:///mlflow.db
"""

from pathlib import Path

import duckdb
import lightgbm as lgb
import mlflow
import numpy as np

PROJECT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_DIR / "vic_demand.duckdb"
TRACKING_URI = f"sqlite:///{(PROJECT_DIR / 'mlflow.db').as_posix()}"
EXPERIMENT = "vic-day-ahead-demand"
REGISTERED_MODEL = "vic_day_ahead_demand_lgbm"

# Time-based split: train on the past, validate on the next period, test on the most recent.
VALIDATION_START = "2026-05-01"
TEST_START = "2026-08-01"

TARGET = "demand_mw"
FEATURES = [
    "lag_24h",
    "lag_168h",
    "rolling_mean_24h",
    "rolling_mean_168h",
    "hour_of_day",
    "local_hour_of_day",
    "day_of_week",
    "is_weekend",
    "month",
    "is_public_holiday",
]

LGBM_PARAMS = {
    "n_estimators": 2000,  # upper limit; early stopping picks the real number
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_child_samples": 20,
    "random_state": 42,
    "verbose": -1,
}
EARLY_STOPPING_ROUNDS = 100


def load_data():
    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        df = con.sql("select * from fct_hourly_demand order by hour_start").df()

    # The first 8 days have no full week of history, so their features are null.
    df = df.dropna(subset=FEATURES).reset_index(drop=True)
    # Floats throughout, so the logged model schema can accept missing values later.
    df[FEATURES] = df[FEATURES].astype(float)
    return df


def split_by_time(df):
    train = df[df["hour_start"] < VALIDATION_START]
    validation = df[(df["hour_start"] >= VALIDATION_START) & (df["hour_start"] < TEST_START)]
    test = df[df["hour_start"] >= TEST_START]
    return train, validation, test


def score(actual, predicted, prefix):
    """MAE in MW and MAPE in percent."""
    actual = np.asarray(actual)
    predicted = np.asarray(predicted)
    return {
        f"{prefix}_mae": float(np.mean(np.abs(actual - predicted))),
        f"{prefix}_mape": float(np.mean(np.abs(actual - predicted) / actual) * 100),
    }


def log_split_info(train, validation, test):
    mlflow.log_params({
        "train_start": str(train["hour_start"].min()),
        "validation_start": VALIDATION_START,
        "test_start": TEST_START,
        "test_end": str(test["hour_start"].max()),
        "n_train": len(train),
        "n_validation": len(validation),
        "n_test": len(test),
    })


def run_naive_baseline(name, lag_column, train, validation, test):
    """A naive forecast just copies an earlier value; nothing is trained."""
    with mlflow.start_run(run_name=name):
        log_split_info(train, validation, test)
        mlflow.log_params({"model_type": "naive", "forecast_column": lag_column})
        metrics = {
            **score(validation[TARGET], validation[lag_column], "validation"),
            **score(test[TARGET], test[lag_column], "test"),
        }
        mlflow.log_metrics(metrics)
    return metrics


def run_lightgbm(train, validation, test):
    with mlflow.start_run(run_name="lightgbm"):
        log_split_info(train, validation, test)
        mlflow.log_params({"model_type": "lightgbm", "features": ",".join(FEATURES), **LGBM_PARAMS})
        mlflow.log_param("early_stopping_rounds", EARLY_STOPPING_ROUNDS)

        model = lgb.LGBMRegressor(**LGBM_PARAMS)
        # Validation data is only used to decide when to stop adding trees.
        # The test set is not seen until the final scoring below.
        model.fit(
            train[FEATURES],
            train[TARGET],
            eval_X=validation[FEATURES],
            eval_y=validation[TARGET],
            eval_metric="l1",
            callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
        )
        mlflow.log_param("best_iteration", model.best_iteration_)

        metrics = {
            **score(validation[TARGET], model.predict(validation[FEATURES]), "validation"),
            **score(test[TARGET], model.predict(test[FEATURES]), "test"),
        }
        mlflow.log_metrics(metrics)

        importance = dict(zip(FEATURES, model.booster_.feature_importance(importance_type="gain")))
        mlflow.log_dict({k: float(v) for k, v in importance.items()}, "feature_importance_gain.json")

        mlflow.lightgbm.log_model(
            model,
            name="model",
            input_example=train[FEATURES].head(5),
            registered_model_name=REGISTERED_MODEL,
        )
    return metrics, importance


def main():
    mlflow.set_tracking_uri(TRACKING_URI)
    mlflow.set_experiment(EXPERIMENT)

    df = load_data()
    train, validation, test = split_by_time(df)
    print(f"train {len(train):,} rows | validation {len(validation):,} | test {len(test):,}")

    results = {
        "naive_last_week": run_naive_baseline("naive_last_week", "lag_168h", train, validation, test),
        "naive_yesterday": run_naive_baseline("naive_yesterday", "lag_24h", train, validation, test),
    }
    results["lightgbm"], importance = run_lightgbm(train, validation, test)

    print(f"\n{'model':<18}{'val MAE':>10}{'val MAPE':>10}{'test MAE':>10}{'test MAPE':>11}")
    for name, m in results.items():
        print(
            f"{name:<18}{m['validation_mae']:>10.0f}{m['validation_mape']:>9.2f}%"
            f"{m['test_mae']:>10.0f}{m['test_mape']:>10.2f}%"
        )

    print("\nLightGBM feature importance (share of total gain):")
    total = sum(importance.values())
    for feature, gain in sorted(importance.items(), key=lambda kv: -kv[1]):
        print(f"  {feature:<20}{gain / total:>6.1%}")


if __name__ == "__main__":
    main()
