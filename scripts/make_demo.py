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
import lightgbm as lgb
import pandas as pd
from forecast_core.calendar import add_holidays
from forecast_core.metrics import wape

FEATURES = ["Units_lag1", "Units_lag4", "PromoFlag", "IsHoliday"]


def train(engine, weeks_back: int = 104) -> lgb.LGBMRegressor:
    """Fit the weekly model on the saved features plus the retail holiday calendar.

    Reads the feature file written by features.py, so run that first. The fitted model
    is saved to models/lgbm_weekly.pkl as well as returned.
    """
    features = pd.read_parquet("outputs/weekly_features.parquet")
    holidays = add_holidays(features, engine)
    model = fit(holidays, weeks_back)
    joblib.dump(model, "models/lgbm_weekly.pkl")
    return model


def fit(df: pd.DataFrame, weeks_back: int) -> lgb.LGBMRegressor:
    """Train on the last `weeks_back` weeks, holding out the final 8 to check accuracy."""

    def split(frame):
        """Last 8 weeks are the hold-out set."""
        cutoff = frame["WeekStart"].max() - pd.Timedelta(weeks=8)
        return frame[frame["WeekStart"] <= cutoff], frame[frame["WeekStart"] > cutoff]

    train_df, test_df = split(df.tail(weeks_back * 7))
    model = lgb.LGBMRegressor(n_estimators=400).fit(train_df[FEATURES], train_df["Units"])
    print("hold-out WAPE", wape(test_df["Units"], model.predict(test_df[FEATURES])))
    return model


def publish(forecast: pd.DataFrame, engine, week: str) -> None:
    """Write the forecast to a CSV for the planners and append it to ml.ForecastOutput."""
    forecast.to_csv(f"outputs/forecast_{week}.csv", index=False)
    forecast.to_sql("ForecastOutput", engine, schema="ml", if_exists="append")


if __name__ == "__main__":
    publish(train(None).predict(None), None, "latest")
'''

REQUIREMENTS_TXT = '''
pandas>=2.1
lightgbm==4.3.0
joblib
sqlalchemy>=2.0
openpyxl
-e ../forecast-core
'''

ENVIRONMENT_YML = '''
name: forecasting
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pandas>=2.1
  - lightgbm=4.3
  - pyodbc
  - pip
  - pip:
      - joblib
'''


LIB_FILES = {
    "common-utils/src/common_utils/__init__.py": "",
    "common-utils/src/common_utils/db.py": '''
        """Shared database helpers."""
        import pandas as pd

        def read_table(engine, name):
            return pd.read_sql(f"SELECT * FROM {name}", engine)

        def store_lookup(engine):
            """StoreId to Region for every store."""
            return pd.read_sql("SELECT StoreId, Region FROM dbo.Store", engine)
    ''',
    "common-utils/src/common_utils/files.py": '''
        """Shared file helpers."""
        import pandas as pd

        def latest_extract(folder):
            return pd.read_csv("extracts/store_list.csv")
    ''',
    "forecast-core/forecast_core/__init__.py": "",
    "forecast-core/forecast_core/calendar.py": '''
        """Retail calendar features."""
        import pandas as pd
        from common_utils.db import store_lookup

        def add_holidays(df, engine):
            """Join public holidays and the retail calendar onto the features, by store region."""
            hol = pd.read_sql("SELECT * FROM ref.Holidays", engine)
            cal = pd.read_sql("SELECT * FROM ref.Calendar", engine)
            return df.merge(hol).merge(store_lookup(engine))
    ''',
    "forecast-core/forecast_core/metrics.py": '''
        """Forecast accuracy metrics."""

        def wape(actual, forecast):
            """Weighted absolute percentage error: total absolute error over total actual."""
            return abs(actual - forecast).sum() / actual.sum()
    ''',
}


LGB_MODEL = """tree
version=v4
objective=regression
feature_names=units_lag_7 units_roll_28 promo_flag
feature_infos=[0:100] [0:100] [0:1]

Tree=0
num_leaves=2

end of trees

feature_importances:
units_lag_7=41
units_roll_28=22
promo_flag=9
"""


def write_report(path: Path) -> None:
    """A small Power BI template (.pbit) reading dbo.SalesDaily, with one measure on one page."""
    import json
    import zipfile
    visual = {"singleVisual": {"visualType": "lineChart", "prototypeQuery": {
        "From": [{"Name": "s", "Entity": "Sales"}],
        "Select": [{"Measure": {"Expression": {"SourceRef": {"Source": "s"}}, "Property": "Total Units"},
                    "Name": "Sales.Total Units"}]},
        "vcObjects": {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'Units by week'"}}}}}]}}}
    layout = {"sections": [{"displayName": "Overview", "visualContainers": [{"config": json.dumps(visual)}]}]}
    model = {"model": {"tables": [{"name": "Sales", "columns": [{"name": "Units", "dataType": "decimal"}],
                                   "measures": [{"name": "Total Units", "expression": "SUM(Sales[Units])",
                                                 "description": "Units sold, net of returns"}],
                                   "partitions": [{"name": "Sales", "source": {"type": "m", "expression": [
                                       "let", '    Source = Sql.Database("DEMO-SQL01", "SalesDW"),',
                                       '    t = Source{[Schema="dbo",Item="SalesDaily"]}[Data]', "in", "    t"]}}]}]}}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(layout).encode("utf-16-le"))
        z.writestr("DataModelSchema", json.dumps(model).encode("utf-16-le"))


def add_knowledge(cat: Catalogue, sales: str) -> None:
    from pdcat import bprojects, curation, keys
    from pdcat.knowledge import save_knowledge, save_metric, save_question
    store = "sql://warehouse/salesdw/dbo.store"
    curation.set_item_info(cat, sales, purpose="Daily unit sales per store and product; the source for forecasting.",
                           grain="store, product and day", refresh="daily", owner="Sam", lifecycle="in use",
                           trust="trusted", freshness_column="SaleDate")
    curation.set_column_info(cat, store, "StoreName", sensitive=False, description="Trading name of the store")
    keys.mark_key(cat, "StoreId", store, "StoreId", master=True)
    save_knowledge(cat, "Freight is always sea freight", "Unless a shipment is marked AIR, freight costs are sea "
                   "freight. Air freight is booked in another system and isn't in these tables.",
                   section="Business rules", triggers="freight, shipping")
    save_knowledge(cat, "Weeks start on Monday", "All weekly figures use ISO weeks starting Monday.",
                   section="Definitions", triggers="week, weekly")
    save_metric(cat, "Forecast accuracy", definition="How close the forecast was to what sold.",
                formula="1 - SUM(ABS(actual - forecast)) / SUM(actual)", sources=[sales], owner="Sam")
    save_question(cat, "What were weekly units by region last quarter?",
                  answer="Sum SalesDaily units by ISO week, joined to Store for the region.",
                  sql="SELECT st.Region, DATEPART(iso_week, s.SaleDate) AS Wk, SUM(s.Units) AS Units\n"
                      "FROM dbo.SalesDaily s JOIN dbo.Store st ON st.StoreId = s.StoreId\n"
                      "WHERE s.SaleDate >= DATEADD(quarter, -1, GETDATE())\nGROUP BY st.Region, "
                      "DATEPART(iso_week, s.SaleDate)", caveats="Returns are negative units.")
    uid = bprojects.save_project(cat, "Demand forecasting", purpose="Weekly store-level sales forecast",
                                 status="active", owner="Sam")
    bprojects.set_member(cat, uid, sales)
    bprojects.set_member(cat, uid, "py://forecasting/src/features.py")


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    project = out / "forecasting"
    (project / "src").mkdir(parents=True, exist_ok=True)
    (project / "outputs").mkdir(exist_ok=True)
    (project / "README.md").write_text("# Forecasting\n\nWeekly store-level sales forecast for the planning team.\n")
    (project / "src" / "features.py").write_text(textwrap.dedent(FEATURES_PY))
    (project / "src" / "train.py").write_text(textwrap.dedent(TRAIN_PY))
    (project / "requirements.txt").write_text(textwrap.dedent(REQUIREMENTS_TXT).lstrip())
    (project / "environment.yml").write_text(textwrap.dedent(ENVIRONMENT_YML).lstrip())
    (out / "forecast-core").mkdir(exist_ok=True)
    (out / "forecast-core" / "pyproject.toml").write_text(
        '[project]\nname = "forecast-core"\nrequires-python = ">=3.10"\ndependencies = ["pandas>=1.5", "holidays"]\n')
    (project / "src" / "regions.py").write_text(textwrap.dedent('''
        """Regional roll-up report."""
        from common_utils.db import store_lookup
        from common_utils.files import latest_extract

        def report(engine):
            return store_lookup(engine).merge(latest_extract("extracts"))
    '''))
    for rel, text in LIB_FILES.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))

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

    (project / "models").mkdir(exist_ok=True)
    (project / "models" / "lgb_model.txt").write_text(LGB_MODEL)
    extracts = out / "extracts"
    extracts.mkdir(exist_ok=True)
    for month, rows in (("01", 310), ("02", 290), ("03", 330), ("05", 301)):  # April is missing
        pd.DataFrame({"StoreId": rng.integers(1, 120, rows), "Units": np.round(rng.gamma(2.0, 6.0, rows), 1)}).to_csv(
            extracts / f"sales_2026-{month}.csv", index=False)
    reports = out / "reports"
    reports.mkdir(exist_ok=True)
    write_report(reports / "Weekly sales.pbit")
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
    for lib in ("common-utils", "forecast-core"):
        cat.add_library(lib, str(out / lib))
        scan_project(cat, PythonSource(name=lib, path=out / lib, role="library", data_files=False,
                                       default_database="warehouse/SalesDW"))
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
    cat.add_folder("reports", str(reports))
    scan_folder(cat, "reports", reports)
    add_knowledge(cat, sales)
    from pdcat.postscan import after_scan
    after_scan(cat, progress=lambda *_: None)
    refresh_tasks(cat)
    cat.close()
    print(f"Demo catalogue: {db}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "pdcat-demo"))
