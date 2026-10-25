import textwrap
from pathlib import Path

import pytest

from pdcat.config import PythonSource, SqlServerSource
from pdcat.scan_python import scan_project
from pdcat.scan_sqlserver import ingest
from pdcat.store import Catalogue

SRC = SqlServerSource(name="warehouse", server="TEST-SERVER", databases=["ForecastDB"])

FAKE_DB = {
    "objects": [
        {"schema_name": "dbo", "object_name": "SalesDaily", "object_type": "U ", "row_count": 1200,
         "description": "Daily unit sales per store", "definition": None},
        {"schema_name": "dbo", "object_name": "Store", "object_type": "U ", "row_count": 40,
         "description": None, "definition": None},
        {"schema_name": "ml", "object_name": "ForecastOutput", "object_type": "U ", "row_count": 0,
         "description": None, "definition": None},
        {"schema_name": "ml", "object_name": "vForecastLatest", "object_type": "V ", "row_count": None,
         "description": None,
         "definition": "CREATE VIEW ml.vForecastLatest AS SELECT * FROM ml.ForecastOutput WHERE run_id = 1"},
        {"schema_name": "ml", "object_name": "usp_LoadForecast", "object_type": "P ", "row_count": None,
         "description": None,
         "definition": "CREATE PROCEDURE ml.usp_LoadForecast AS INSERT INTO ml.ForecastOutput SELECT * FROM dbo.SalesDaily"},
    ],
    "columns": [
        {"schema_name": "dbo", "object_name": "SalesDaily", "column_name": "StoreId", "column_id": 1,
         "type_name": "int", "max_length": 4, "precision": 10, "scale": 0, "is_nullable": False, "description": None},
        {"schema_name": "dbo", "object_name": "SalesDaily", "column_name": "Units", "column_id": 2,
         "type_name": "decimal", "max_length": 9, "precision": 18, "scale": 2, "is_nullable": True,
         "description": "Units sold, net of returns"},
        {"schema_name": "dbo", "object_name": "Store", "column_name": "Region", "column_id": 1,
         "type_name": "nvarchar", "max_length": 100, "precision": 0, "scale": 0, "is_nullable": True,
         "description": None},
    ],
}

FEATURES_PY = '''
"""Build weekly sales features for the forecast model."""
import pandas as pd

TABLE = "dbo.SalesDaily"

def load(engine):
    q = f"SELECT s.Units FROM {TABLE} s JOIN Store st ON st.StoreId = s.StoreId"
    return pd.read_sql(q, engine)

def save(df, engine):
    df.to_sql("ForecastOutput", engine, schema="ml", if_exists="append")
    df.to_parquet("outputs/features.parquet")
    pd.read_sql("SELECT * FROM staging.Mystery", engine)
'''


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "forecasting"
    (root / "src").mkdir(parents=True)
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / "README.md").write_text("# Forecasting\n\nWeekly store-level sales forecast.\n")
    (root / "src" / "features.py").write_text(textwrap.dedent(FEATURES_PY))
    (root / ".venv" / "lib" / "ignored.py").write_text("x = 'SELECT * FROM dbo.Ignored'\n")
    return root


@pytest.fixture
def catalogue(tmp_path: Path, project: Path) -> Catalogue:
    cat = Catalogue(tmp_path / "catalogue.db")
    with cat.scan(SRC.name):
        ingest(cat, SRC, "ForecastDB", FAKE_DB)
    scan_project(cat, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    yield cat
    cat.close()
