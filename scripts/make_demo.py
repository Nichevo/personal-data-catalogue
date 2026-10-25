"""Build a demo catalogue from made-up data, for trying the app or taking screenshots.

    python scripts/make_demo.py C:/temp/pdcat-demo
    streamlit run src/pdcat/app.py -- --db C:/temp/pdcat-demo/catalogue.db

Nothing here touches a real database: the SQL Server metadata is fake.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pdcat.config import PythonSource, SqlServerSource  # noqa: E402
from pdcat.scan_files import scan_folder  # noqa: E402
from pdcat.scan_python import scan_project  # noqa: E402
from pdcat.review import refresh_tasks  # noqa: E402
from pdcat.scan_sqlserver import ingest  # noqa: E402
from pdcat.store import Catalogue  # noqa: E402

SERVER = SqlServerSource(name="warehouse", server="DEMO-SQL01", databases=["SalesDW"])


def table(schema, name, rows, description=None, kind="U ", definition=None):
    return {"schema_name": schema, "object_name": name, "object_type": kind, "row_count": rows,
            "description": description, "definition": definition, "modify_date": "2026-06-01 02:00:00"}


def col(schema, obj, name, i, type_name, nullable=False, description=None, length=0, precision=0, scale=0):
    return {"schema_name": schema, "object_name": obj, "column_name": name, "column_id": i, "type_name": type_name,
            "max_length": length, "precision": precision, "scale": scale, "is_nullable": nullable,
            "description": description}


FAKE_DB = {
    "objects": [
        table("dbo", "SalesDaily", 18_250_000, "Daily unit sales per store and product, loaded nightly from the POS feed."),
        table("dbo", "Store", 412, "One row per store, with region and open/close dates."),
        table("dbo", "Product", 9_870, "Product master from the merchandising system."),
        table("ref", "Calendar", 7_300, "Retail calendar: weeks, periods, holidays."),
        table("ref", "Holidays", 240),
        table("ml", "ForecastFeatures", 2_100_000),
        table("ml", "ForecastOutput", 1_480_000, "Weekly forecast per store and product, written by the forecast job."),
        table("staging", "PosFeed", 95_000),
        table("ml", "vForecastLatest", None, kind="V ",
              definition="CREATE VIEW ml.vForecastLatest AS SELECT f.* FROM ml.ForecastOutput f "
                         "JOIN dbo.Store s ON s.StoreId = f.StoreId WHERE f.RunId = (SELECT MAX(RunId) FROM ml.ForecastOutput)"),
        table("dbo", "usp_LoadSalesDaily", None, kind="P ",
              definition="CREATE PROCEDURE dbo.usp_LoadSalesDaily AS "
                         "DELETE FROM dbo.SalesDaily WHERE SaleDate >= DATEADD(day, -3, GETDATE()); "
                         "INSERT INTO dbo.SalesDaily SELECT * FROM staging.PosFeed"),
    ],
    "columns": [
        col("dbo", "SalesDaily", "SaleDate", 1, "date"),
        col("dbo", "SalesDaily", "StoreId", 2, "int"),
        col("dbo", "SalesDaily", "ProductId", 3, "int"),
        col("dbo", "SalesDaily", "Units", 4, "decimal", True, "Units sold, net of returns", 9, 18, 2),
        col("dbo", "SalesDaily", "Revenue", 5, "decimal", True, "Revenue ex GST", 9, 18, 2),
        col("dbo", "SalesDaily", "PromoFlag", 6, "bit", True),
        col("dbo", "Store", "StoreId", 1, "int"),
        col("dbo", "Store", "StoreName", 2, "nvarchar", length=200),
        col("dbo", "Store", "Region", 3, "nvarchar", True, length=100),
        col("dbo", "Store", "OpenDate", 4, "date", True),
        col("ml", "ForecastOutput", "RunId", 1, "int"),
        col("ml", "ForecastOutput", "WeekStart", 2, "date"),
        col("ml", "ForecastOutput", "StoreId", 3, "int"),
        col("ml", "ForecastOutput", "ProductId", 4, "int"),
        col("ml", "ForecastOutput", "ForecastUnits", 5, "decimal", True, length=9, precision=18, scale=2),
    ],
}

FEATURES_PY = '''
"""Build weekly sales features for the store-level forecast model."""
import pandas as pd

SALES = "dbo.SalesDaily"

def load(engine, start):
    sql = f"""
        SELECT s.SaleDate, s.StoreId, s.ProductId, s.Units, s.PromoFlag, st.Region
        FROM {SALES} s
        JOIN dbo.Store st ON st.StoreId = s.StoreId
        JOIN ref.Calendar c ON c.CalendarDate = s.SaleDate
        WHERE s.SaleDate >= '{start}'
    """
    return pd.read_sql(sql, engine)

def save(features, engine):
    features.to_parquet("outputs/weekly_features.parquet")
    features.to_sql("ForecastFeatures", engine, schema="ml", if_exists="replace")
'''

TRAIN_PY = '''
"""Train the LightGBM forecast model and write the weekly forecast."""
import joblib
import pandas as pd

def train(engine):
    features = pd.read_parquet("outputs/weekly_features.parquet")
    holidays = pd.read_sql_table("Holidays", engine, schema="ref")
    model = fit(features, holidays)
    joblib.dump(model, "models/lgbm_weekly.pkl")
    return model

def publish(forecast, engine, week):
    forecast.to_csv(f"outputs/forecast_{week}.csv", index=False)
    forecast.to_sql("ForecastOutput", engine, schema="ml", if_exists="append")
'''


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    project = out / "forecasting"
    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "outputs").mkdir(exist_ok=True)
    (project / "README.md").write_text("# Forecasting\n\nWeekly store-level sales forecast for the planning team.\n")
    (project / "src" / "features.py").write_text(textwrap.dedent(FEATURES_PY))
    (project / "src" / "train.py").write_text(textwrap.dedent(TRAIN_PY))

    rng = np.random.default_rng(7)
    n = 5_000
    feats = pd.DataFrame({
        "SaleDate": pd.to_datetime("2026-01-05") + pd.to_timedelta(rng.integers(0, 280, n), unit="D"),
        "StoreId": rng.integers(1, 120, n), "ProductId": rng.integers(1000, 1600, n),
        "Units": np.round(rng.gamma(2.0, 6.0, n), 2), "PromoFlag": rng.random(n) < 0.15,
        "Region": rng.choice(["North", "South", "East", "West"], n),
    })
    feats.loc[rng.random(n) < 0.03, "Units"] = None
    feats.to_parquet(project / "outputs" / "weekly_features.parquet")
    for week in ("2026-W41", "2026-W42"):
        pd.DataFrame({"StoreId": rng.integers(1, 120, 800), "ProductId": rng.integers(1000, 1600, 800),
                      "ForecastUnits": np.round(rng.gamma(2.0, 6.0, 800), 1)}).to_csv(
            project / "outputs" / f"forecast_{week}.csv", index=False)

    extracts = out / "extracts"
    extracts.mkdir(exist_ok=True)
    pd.DataFrame({"StoreId": range(1, 121), "Region": rng.choice(["North", "South", "East", "West"], 120),
                  "Format": rng.choice(["Metro", "Suburban", "Regional"], 120)}).to_csv(extracts / "store_list.csv", index=False)

    db = out / "catalogue.db"
    db.unlink(missing_ok=True)
    cat = Catalogue(db)
    with cat.scan(SERVER.name):
        ingest(cat, SERVER, "SalesDW", FAKE_DB)
    # A second scan a while later: one table changed, one appeared.
    later = {"objects": [dict(o) for o in FAKE_DB["objects"]], "columns": FAKE_DB["columns"]}
    later["objects"][0]["modify_date"] = "2026-10-20 06:12:00"
    later["objects"].append(table("dbo", "PromoCalendar", 1_250))
    with cat.scan(SERVER.name):
        ingest(cat, SERVER, "SalesDW", later)
    cat.add_folder("extracts", str(extracts))
    scan_folder(cat, "extracts", extracts)
    scan_project(cat, PythonSource(name="forecasting", path=project, default_database="warehouse/SalesDW"))

    sales = "sql://warehouse/salesdw/dbo.salesdaily"
    cat.save_profile(sales, [
        {"name": "SaleDate", "data_type": "date", "rows": 18_250_000, "nulls": 0, "distinct": 1_826,
         "min": "2021-10-01", "max": "2026-10-23", "top": [["2025-12-23", 11_240], ["2025-12-22", 11_198]]},
        {"name": "StoreId", "data_type": "int", "rows": 18_250_000, "nulls": 0, "distinct": 412, "min": 1, "max": 412,
         "mean": 205.3, "top": [["17", 61_804], ["3", 60_112]]},
        {"name": "ProductId", "data_type": "int", "rows": 18_250_000, "nulls": 0, "distinct": 9_870, "min": 1000,
         "max": 10870, "mean": 5_402.1, "top": [["1042", 4_120]]},
        {"name": "Units", "data_type": "decimal(18,2)", "rows": 18_250_000, "nulls": 21_400, "distinct": 4_311,
         "min": -24, "max": 2_400, "mean": 11.8, "top": [["1.00", 3_402_118], ["2.00", 1_980_442]]},
        {"name": "Revenue", "data_type": "decimal(18,2)", "rows": 18_250_000, "nulls": 21_400, "distinct": 88_120,
         "min": -310.5, "max": 48_000, "mean": 64.2, "top": [["4.50", 402_118]]},
        {"name": "PromoFlag", "data_type": "bit", "rows": 18_250_000, "nulls": 1_200_000, "distinct": 2,
         "top": [["False", 14_900_000], ["True", 2_150_000]]},
    ], "whole table for counts and ranges; mean and top values from first 10,000 rows")

    cat.add_note(sales, "Loaded nightly at 2am by `dbo.usp_LoadSalesDaily`, which reloads the **last 3 days**. "
                        "Monday loads are often late, so check before the forecast job runs.", "refresh, forecasting")
    cat.add_note(f"{sales}#Units", "Negative units are returns that couldn't be matched to a sale. "
                                   "Store 214 has a spike on 2026-03-02 (POS double-post).", "dq", note_type="issue")
    cat.add_note(f"{sales}#PromoFlag", "NULL before 2023-07 when promo tracking started.", "dq")
    cat.add_note(sales, "StoreId   Region  Units/day (median)\n17        North   148\n3         South   131\n214       West    12",
                 note_type="example")
    cat.add_note("sql://warehouse/salesdw/ml.forecastoutput", "Keep RunIds for 12 weeks; older runs are archived.")
    cat.save_snippet("Weekly units by region",
                     "SELECT st.Region, DATEPART(week, s.SaleDate) AS Wk, SUM(s.Units) AS Units\n"
                     "FROM dbo.SalesDaily s\nJOIN dbo.Store st ON st.StoreId = s.StoreId\n"
                     "WHERE s.SaleDate >= '2026-01-01'\nGROUP BY st.Region, DATEPART(week, s.SaleDate)",
                     description="Quick check that the forecast totals line up with actuals by region.",
                     tags="forecasting, check", database="warehouse/SalesDW")
    cat.add_note("sql://warehouse/salesdw/ml.forecastoutput#ForecastUnits",
                 "Run 212 wrote duplicate rows for 30 stores.", "dq", note_type="issue")
    cat.save_profile("sql://warehouse/salesdw/ml.forecastoutput", [
        {"name": "RunId", "data_type": "int", "rows": 1_480_000, "nulls": 0, "distinct": 12, "min": 201, "max": 212},
        {"name": "WeekStart", "data_type": "date", "rows": 1_480_000, "nulls": 0, "distinct": 26,
         "min": "2026-05-04", "max": "2099-12-28"},
        {"name": "StoreId", "data_type": "int", "rows": 1_480_000, "nulls": 0, "distinct": 412, "min": 1, "max": 412},
        {"name": "ProductId", "data_type": "int", "rows": 1_480_000, "nulls": 0, "distinct": 9_870, "min": 1000,
         "max": 10870},
        {"name": "ForecastUnits", "data_type": "decimal(18,2)", "rows": 1_480_000, "nulls": 820_000, "distinct": 5_200,
         "min": 0, "max": 3_100},
    ], "whole table for counts and ranges; mean and top values from first 10,000 rows")
    refresh_tasks(cat)
    cat.close()
    print(f"Demo catalogue: {db}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "pdcat-demo"))
