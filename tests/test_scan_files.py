import json

from pdcat.config import EXAMPLE_CONFIG, load_config
from pdcat.scan_python import scan_file, scan_python_source


def test_notebook_cells_and_sql_magic(tmp_path):
    nb = {"cells": [
        {"cell_type": "markdown", "source": ["# Model training\n", "Trains the weekly model."]},
        {"cell_type": "code", "source": ["%matplotlib inline\n", "import joblib\n",
                                         "df = pd.read_sql('SELECT * FROM ml.Features', eng)\n",
                                         "joblib.dump(model, 'models/weekly.pkl')"]},
        {"cell_type": "code", "source": ["%%sql\n", "SELECT * FROM dbo.Store"]},
        {"cell_type": "code", "source": ["def broken(:"]},
    ]}
    path = tmp_path / "train.ipynb"
    path.write_text(json.dumps(nb))
    found = scan_file(path)
    assert found.description == "Model training: Trains the weekly model."
    assert set(found.refs) == {("reads", "table:ml.features", 0), ("writes", "file:models/weekly.pkl", 0),
                               ("reads", "table:dbo.store", 0)}
    assert found.attrs["imports"] == ["joblib"]
    assert found.attrs["parse_error"] == "Could not parse cell 4"


def test_docstrings_are_not_treated_as_sql():
    found = scan_python_source('"""Select the rows from the warehouse table."""\ndef f():\n    """select x from y"""\n')
    assert found.refs == []


def test_syntax_error_is_reported_not_raised():
    assert "line 1" in scan_python_source("def (:\n").attrs["parse_error"]


def test_open_modes_and_read_sql_table():
    found = scan_python_source(
        "open('out.txt', 'w')\nopen('in.json')\npd.read_sql_table('Sales', eng)\npd.read_csv(path_var)\n")
    assert {(r, ref) for r, ref, _ in found.refs} == {
        ("writes", "file:out.txt"), ("reads", "file:in.json"), ("reads", "table:sales")}


def test_example_config_loads(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE_CONFIG)
    cfg = load_config(path)
    assert cfg.sqlserver[0].server == "YOUR-SERVER\\INSTANCE"
    assert cfg.python[0].default_database == "warehouse/YourDatabase"
