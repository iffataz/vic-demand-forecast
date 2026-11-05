"""Run the whole pipeline in order: download, transform and test, train.

Usage (with the virtual environment active):
    python run_pipeline.py

Each step stops the pipeline if it fails, so a broken step never feeds the next one.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable
DBT = str(Path(PYTHON).parent / "dbt")  # the dbt installed in the same environment

STEPS = [
    ("Download AEMO data", [PYTHON, "scripts/download_aemo.py"]),
    ("Build and test dbt models (seed, staging, curated)", [DBT, "build"]),
    ("Train baselines and LightGBM, log to MLflow", [PYTHON, "ml/train.py"]),
]


def main():
    for name, command in STEPS:
        print(f"\n=== {name} ===", flush=True)
        subprocess.run(command, cwd=PROJECT_DIR, check=True)
    print("\nDone. View runs with: mlflow ui --backend-store-uri sqlite:///mlflow.db")


if __name__ == "__main__":
    main()
