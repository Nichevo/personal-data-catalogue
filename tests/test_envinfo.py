import textwrap

from pdcat import envinfo
from pdcat.envinfo import (compare, libraries_overview, parse_conda, parse_pipfile, parse_pyproject,
                           parse_requirements, parse_setup_py, project_environment, read_environment)

ENV_YML = """
name: forecasting
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pandas>=2.1
  - conda-forge::lightgbm=4.3
  - pip
  - pip:
      - joblib==1.4.0
      - -e ../forecast-core
"""


def test_requirements():
    deps, editable = parse_requirements(textwrap.dedent("""
        # comment
        pandas>=2.0  # inline
        scikit-learn[alldeps]==1.4.2 ; python_version > "3.9"
        -r other.txt
        --index-url https://example.invalid/simple
        -e ../common-utils
        mylib @ git+https://example.invalid/mylib.git
        ./wheels/thing.whl
    """), "requirements.txt")
    assert [(d["name"], d["version"]) for d in deps] == [("pandas", ">=2.0"), ("scikit-learn", "==1.4.2"),
                                                         ("mylib", "")]
    assert editable == ["../common-utils", "./wheels/thing.whl"]


def test_conda_with_and_without_pyyaml(monkeypatch):
    for _ in range(2):
        env = parse_conda(ENV_YML, "environment.yml")
        assert env["name"] == "forecasting" and env["channels"] == ["conda-forge"] and env["python"] == "3.11"
        assert [(d["name"], d["version"], d["manager"]) for d in env["deps"]] == [
            ("pandas", ">=2.1", "conda"), ("lightgbm", "4.3", "conda"), ("pip", "", "conda"),
            ("joblib", "==1.4.0", "pip")]
        assert env["editable"] == ["../forecast-core"]
        monkeypatch.setattr(envinfo, "yaml", None)  # second pass uses the built-in reader


def test_pyproject_setup_and_pipfile():
    p = parse_pyproject(textwrap.dedent("""
        [project]
        name = "x"
        requires-python = ">=3.10"
        dependencies = ["pandas>=2", "pyodbc"]
        [project.optional-dependencies]
        dev = ["pytest"]
        [tool.poetry.dependencies]
        python = "^3.11"
        numpy = "^1.26"
        [tool.poetry.group.dev.dependencies]
        black = "*"
    """), "pyproject.toml")
    assert p["python"] == ">=3.10"
    assert [(d["name"], d.get("group", "")) for d in p["deps"]] == [
        ("pandas", ""), ("pyodbc", ""), ("pytest", "dev"), ("numpy", ""), ("black", "dev")]
    assert [d["name"] for d in parse_setup_py("setup(name='x', install_requires=['requests>=2', 'pyyaml'])",
                                              "setup.py")] == ["requests", "pyyaml"]
    assert [(d["name"], d["version"]) for d in parse_pipfile('[packages]\nflask = "*"\n[dev-packages]\npytest = ">=8"\n',
                                                            "Pipfile")] == [("flask", ""), ("pytest", ">=8")]


def test_compare_maps_import_names_to_packages():
    env = {"files": ["requirements.txt"], "deps": [
        {"name": "scikit-learn", "version": "", "file": "requirements.txt", "manager": "pip"},
        {"name": "PyYAML", "version": "", "file": "requirements.txt", "manager": "pip"},
        {"name": "openpyxl", "version": "", "file": "requirements.txt", "manager": "pip"},
        {"name": "pytest", "version": "", "file": "requirements.txt", "manager": "pip"}]}
    out = compare(env, {"sklearn": ["a.py"], "yaml": ["a.py"], "os": ["a.py"], "requests": ["b.py"],
                        "common": ["a.py"]}, home_built={"common"})
    assert {r["module"] for r in out["third_party"]} == {"sklearn", "yaml"}
    assert [r["package"] for r in out["undeclared"]] == ["requests"]
    assert [d["name"] for d in out["unused"]] == ["openpyxl"]            # pytest is tooling


def test_scan_records_environment(catalogue, project):
    from pdcat.config import PythonSource
    from pdcat.scan_python import scan_project

    (project / "environment.yml").write_text(ENV_YML)
    (project / "requirements").mkdir()
    (project / "requirements" / "base.txt").write_text("sqlalchemy>=2\n")
    scan_project(catalogue, PythonSource(name="forecasting", path=project))
    assert read_environment(project)["files"] == ["requirements/base.txt", "environment.yml"]
    env = project_environment(catalogue, "forecasting")
    assert env["python"] == [{"file": "environment.yml", "version": "3.11"}]
    assert {r["module"] for r in env["third_party"]} == {"pandas"}
    assert {d["name"] for d in env["unused"]} == {"lightgbm", "joblib", "sqlalchemy"}
    rows = {r["package"]: r for r in libraries_overview(catalogue)}
    assert rows["pandas"]["forecasting"] == ">=2.1"


def test_conda_environment_file_matches_pyproject():
    """environment.yml must install everything pyproject.toml needs, so `pip install -e . --no-deps` works."""
    from pathlib import Path

    root = Path(__file__).parents[1]
    conda = parse_conda((root / "environment.yml").read_text(), "environment.yml")
    project = parse_pyproject((root / "pyproject.toml").read_text(), "pyproject.toml")
    in_conda = {envinfo.normalise(d["name"]) for d in conda["deps"]}
    needed = {envinfo.normalise(d["name"]) for d in project["deps"]}
    assert needed <= in_conda, needed - in_conda
    assert conda["name"] == "pdcat" and conda["channels"] == ["conda-forge"]
