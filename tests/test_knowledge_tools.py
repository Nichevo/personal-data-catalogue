"""Business projects, coverage, the feature dictionary, and the knowledge pages."""
import textwrap
from pathlib import Path

import pytest

from pdcat import bprojects as B
from pdcat import coverage, curation
from pdcat import mlfeatures as M
from pdcat.config import PythonSource
from pdcat.scan_python import scan_project

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"
SCRIPT = "py://forecasting/src/features.py"

LGB_MODEL = textwrap.dedent("""\
    tree
    version=v4
    num_class=1
    objective=regression
    feature_names=sales_lag_7 sales_roll_28 next_week_sales
    feature_infos=[0:10] [0:10] [0:10]

    Tree=0
    num_leaves=2

    Tree=1
    num_leaves=2

    end of trees

    feature_importances:
    sales_lag_7=12
    sales_roll_28=5

    parameters:
    [boosting: gbdt]
    end of parameters
    """)

FEATURE_CODE = textwrap.dedent('''
    import pandas as pd

    def build_features(df):
        df["sales_lag_7"] = df.groupby("StoreId")["Units"].shift(7)
        df["sales_roll_28"] = df["Units"].rolling(28).mean()
        df["next_week_sales"] = df["Units"].shift(-7)
        return df
    ''')


def test_business_projects_and_suggestions(catalogue):
    uid = B.save_project(catalogue, "Sales forecasting", purpose="Weekly store sales forecast", status="active")
    B.set_member(catalogue, uid, SCRIPT)
    sugg = {s["key"]: s["why"] for s in B.suggest(catalogue, uid)}
    assert SALES in sugg and "by a member" in sugg[SALES]
    B.set_member(catalogue, uid, SALES)
    B.set_member(catalogue, uid, STORE, "rejected")
    assert STORE not in {s["key"] for s in B.suggest(catalogue, uid)}  # rejected ones stay hidden
    assert [p["name"] for p in B.projects_for(catalogue, SALES)] == ["Sales forecasting"]
    assert B.list_projects(catalogue)[0]["members"] == 2
    hit = [r for r in catalogue.search("forecasting") if r["key"] == f"bp://{uid}"]
    assert hit and hit[0]["name"] == "Sales forecasting"  # its name, not its key
    with pytest.raises(ValueError):
        B.save_project(catalogue, " ")


def test_coverage_scores_and_next(catalogue):
    before = {r["key"]: r for r in coverage.scores(catalogue)}
    assert before[SALES]["score"] > before[STORE]["score"]  # SalesDaily has a description and one column described
    curation.set_item_info(catalogue, STORE, purpose="Store list", grain="store", owner="Sam", refresh="weekly",
                           lifecycle="in use")
    curation.set_column_info(catalogue, STORE, "Region", description="Sales region")
    after = {r["key"]: r for r in coverage.scores(catalogue)}
    assert after[STORE]["score"] == 90  # everything but notes
    s = coverage.summary(catalogue)
    assert 0 < s["overall"] <= 100 and s["items"] == len(after)
    nxt = coverage.next_to_document(catalogue, 3)
    assert len(nxt) <= 3 and STORE not in {r["key"] for r in nxt}


def test_lightgbm_model_file_is_read(tmp_path):
    p = tmp_path / "lgb_model.txt"
    p.write_text(LGB_MODEL)
    info = M.read_model_file(p)
    assert info["features"] == ["sales_lag_7", "sales_roll_28", "next_week_sales"]
    assert info["importance"] == {"sales_lag_7": 12.0, "sales_roll_28": 5.0} and info["trees"] == 2
    pkl = tmp_path / "model.pkl"
    pkl.write_bytes(b"\x80\x04not really a pickle")
    assert M.read_model_file(pkl)["format"] == "pickle"  # listed, never opened


def test_feature_suggestions_from_code_and_models(catalogue, project):
    (project / "src" / "train.py").write_text(FEATURE_CODE)
    (project / "models").mkdir()
    (project / "models" / "lgb_model.txt").write_text(LGB_MODEL)
    scan_project(catalogue, PythonSource(name="forecasting", path=project, default_database="warehouse/ForecastDB"))
    assert M.suggest_features(catalogue) >= 3
    sugg = {f["name"]: f for f in M.list_features(catalogue, state="suggested")}
    lag = sugg["sales_lag_7"]
    assert "7" in (lag["window"] or "") and lag["models"] == ["py://forecasting/models/lgb_model.txt"]
    assert lag["importance"]["py://forecasting/models/lgb_model.txt"] == 12.0
    assert sugg["next_week_sales"]["known_at_prediction"] == "no"
    # Confirm two, reject one; a fresh look keeps those decisions.
    M.set_state(catalogue, lag["uid"], "confirmed")
    M.set_state(catalogue, sugg["next_week_sales"]["uid"], "confirmed")
    M.set_state(catalogue, sugg["sales_roll_28"]["uid"], "rejected")
    M.suggest_features(catalogue)
    names = {f["name"] for f in M.list_features(catalogue, state="suggested")}
    assert not names & {"sales_lag_7", "sales_roll_28", "next_week_sales"}
    c = M.checks(catalogue)
    assert [f["name"] for f in c["leakage"]] == ["next_week_sales"]


def test_describe_everywhere(catalogue, tmp_path):
    import pandas as pd

    from pdcat.scan_files import scan_folder
    folder = tmp_path / "x"
    folder.mkdir()
    pd.DataFrame({"StoreId": [1], "Region": ["N"]}).to_csv(folder / "stores.csv", index=False)
    scan_folder(catalogue, "x", folder)
    names = {n["column_name"]: n for n in curation.undescribed_column_names(catalogue)}
    assert names["storeid"]["places"] >= 2
    n = curation.describe_everywhere(catalogue, "storeid", "Store number, company-wide")
    assert n == names["storeid"]["undescribed"]
    assert curation.column_description(catalogue, SALES, "StoreId") == "Store number, company-wide"
