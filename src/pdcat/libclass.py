"""Sort every import into your own code, home-built libraries, third-party packages
and the standard library.

The answer doesn't depend on the Python running this app: a package counts as
third-party if any scanned project declares it (requirements.txt,
environment.yml, pyproject.toml…), if it is on the list of well-known packages
below, or if it happens to be installed here. Home-built means a module found
in another catalogued project or library.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .envinfo import _STDLIB, ALIASES, normalise, package_for, project_environment
from .store import Catalogue

OWN, HOME, THIRD, STDLIB, UNKNOWN = "own", "home-built", "third-party", "standard library", "unknown"
CLASS_LABELS = {OWN: "This project", HOME: "Home-built library", THIRD: "Third-party package",
                STDLIB: "Python standard library", UNKNOWN: "Couldn't place"}

# Import name -> (package, what it is for). Purpose labels show on the package box in the code map.
KNOWN: dict[str, tuple[str, str]] = {
    "pandas": ("pandas", "data frames"), "numpy": ("numpy", "arrays and maths"),
    "polars": ("polars", "data frames"), "pyarrow": ("pyarrow", "Arrow and Parquet files"),
    "scipy": ("scipy", "scientific maths"), "statsmodels": ("statsmodels", "statistical models"),
    "sklearn": ("scikit-learn", "machine learning"), "lightgbm": ("lightgbm", "gradient boosting models"),
    "xgboost": ("xgboost", "gradient boosting models"), "catboost": ("catboost", "gradient boosting models"),
    "torch": ("torch", "deep learning"), "tensorflow": ("tensorflow", "deep learning"),
    "keras": ("keras", "deep learning"), "prophet": ("prophet", "time series forecasting"),
    "pmdarima": ("pmdarima", "time series forecasting"), "sktime": ("sktime", "time series"),
    "darts": ("darts", "time series forecasting"), "statsforecast": ("statsforecast", "time series forecasting"),
    "optuna": ("optuna", "hyperparameter tuning"), "hyperopt": ("hyperopt", "hyperparameter tuning"),
    "shap": ("shap", "model explanations"), "mlflow": ("mlflow", "experiment tracking"),
    "joblib": ("joblib", "saving models, parallel jobs"), "matplotlib": ("matplotlib", "charts"),
    "seaborn": ("seaborn", "charts"), "plotly": ("plotly", "charts"), "altair": ("altair", "charts"),
    "bokeh": ("bokeh", "charts"), "streamlit": ("streamlit", "web apps"), "dash": ("dash", "web apps"),
    "flask": ("flask", "web apps"), "fastapi": ("fastapi", "web APIs"), "requests": ("requests", "HTTP"),
    "httpx": ("httpx", "HTTP"), "pyodbc": ("pyodbc", "database connections"),
    "sqlalchemy": ("sqlalchemy", "database connections"), "pymssql": ("pymssql", "database connections"),
    "psycopg2": ("psycopg2-binary", "database connections"), "duckdb": ("duckdb", "SQL on files"),
    "openpyxl": ("openpyxl", "Excel files"), "xlrd": ("xlrd", "Excel files"), "xlsxwriter": ("xlsxwriter", "Excel files"),
    "pyxlsb": ("pyxlsb", "Excel files"), "win32com": ("pywin32", "Windows and Office automation"),
    "pythoncom": ("pywin32", "Windows and Office automation"), "yaml": ("pyyaml", "config files"),
    "toml": ("toml", "config files"), "dotenv": ("python-dotenv", "config files"),
    "dateutil": ("python-dateutil", "dates"), "pytz": ("pytz", "time zones"), "holidays": ("holidays", "holiday calendars"),
    "tqdm": ("tqdm", "progress bars"), "click": ("click", "command lines"), "typer": ("typer", "command lines"),
    "pydantic": ("pydantic", "data validation"), "great_expectations": ("great-expectations", "data validation"),
    "pandera": ("pandera", "data validation"), "pytest": ("pytest", "tests"), "bs4": ("beautifulsoup4", "web pages"),
    "lxml": ("lxml", "XML"), "PIL": ("pillow", "images"), "cv2": ("opencv-python", "images"),
    "networkx": ("networkx", "graphs"), "geopandas": ("geopandas", "maps"), "shapely": ("shapely", "maps"),
    "boto3": ("boto3", "AWS"), "azure": ("azure", "Azure"), "google": ("google-api-core", "Google Cloud"),
    "dask": ("dask", "parallel data frames"), "pyspark": ("pyspark", "Spark"), "numba": ("numba", "fast numeric code"),
    "nltk": ("nltk", "text"), "spacy": ("spacy", "text"), "transformers": ("transformers", "language models"),
    "openai": ("openai", "AI service"), "jinja2": ("jinja2", "templates"), "IPython": ("ipython", "notebooks"),
    "ipywidgets": ("ipywidgets", "notebooks"), "tabulate": ("tabulate", "tables as text"),
    "pyodbc_driver": ("pyodbc", "database connections"), "pbixray": ("pbixray", "Power BI files"),
    "category_encoders": ("category-encoders", "feature encoding"), "feature_engine": ("feature-engine", "features"),
    "imblearn": ("imbalanced-learn", "machine learning"), "skforecast": ("skforecast", "time series forecasting"),
    "mlforecast": ("mlforecast", "time series forecasting"), "neuralprophet": ("neuralprophet", "time series forecasting"),
    "tsfresh": ("tsfresh", "time series features"), "pyodps": ("pyodps", "database connections"),
}


# Where the documentation lives for packages whose docs aren't on PyPI's front page. Anything else gets its
# PyPI page, which links to the project's own docs.
DOCS: dict[str, str] = {
    "pandas": "https://pandas.pydata.org/docs/", "numpy": "https://numpy.org/doc/stable/",
    "polars": "https://docs.pola.rs/", "pyarrow": "https://arrow.apache.org/docs/python/",
    "scipy": "https://docs.scipy.org/doc/scipy/", "statsmodels": "https://www.statsmodels.org/stable/",
    "scikit-learn": "https://scikit-learn.org/stable/user_guide.html", "lightgbm": "https://lightgbm.readthedocs.io/",
    "xgboost": "https://xgboost.readthedocs.io/", "catboost": "https://catboost.ai/docs/",
    "torch": "https://pytorch.org/docs/stable/", "tensorflow": "https://www.tensorflow.org/api_docs/python",
    "keras": "https://keras.io/api/", "prophet": "https://facebook.github.io/prophet/docs/quick_start.html",
    "pmdarima": "https://alkaline-ml.com/pmdarima/", "sktime": "https://www.sktime.net/en/stable/",
    "darts": "https://unit8co.github.io/darts/", "statsforecast": "https://nixtlaverse.nixtla.io/statsforecast/",
    "optuna": "https://optuna.readthedocs.io/", "shap": "https://shap.readthedocs.io/",
    "mlflow": "https://mlflow.org/docs/latest/", "joblib": "https://joblib.readthedocs.io/",
    "matplotlib": "https://matplotlib.org/stable/", "seaborn": "https://seaborn.pydata.org/",
    "plotly": "https://plotly.com/python/", "altair": "https://altair-viz.github.io/",
    "streamlit": "https://docs.streamlit.io/", "requests": "https://requests.readthedocs.io/",
    "httpx": "https://www.python-httpx.org/", "pyodbc": "https://github.com/mkleehammer/pyodbc/wiki",
    "sqlalchemy": "https://docs.sqlalchemy.org/", "openpyxl": "https://openpyxl.readthedocs.io/",
    "xlsxwriter": "https://xlsxwriter.readthedocs.io/", "beautifulsoup4": "https://www.crummy.com/software/BeautifulSoup/bs4/doc/",
    "selenium": "https://www.selenium.dev/documentation/", "scrapy": "https://docs.scrapy.org/",
    "pyspark": "https://spark.apache.org/docs/latest/api/python/", "dask": "https://docs.dask.org/",
    "duckdb": "https://duckdb.org/docs/", "pytest": "https://docs.pytest.org/", "pydantic": "https://docs.pydantic.dev/",
    "fastapi": "https://fastapi.tiangolo.com/", "flask": "https://flask.palletsprojects.com/",
    "dash": "https://dash.plotly.com/", "imbalanced-learn": "https://imbalanced-learn.org/stable/",
    "skforecast": "https://skforecast.org/", "mlforecast": "https://nixtlaverse.nixtla.io/mlforecast/",
    "neuralprophet": "https://neuralprophet.com/", "tsfresh": "https://tsfresh.readthedocs.io/",
}


def docs_url(package: str) -> str:
    """The package's documentation, or its PyPI page when the docs site isn't known."""
    norm = normalise(package)
    for pkg, url in DOCS.items():
        if normalise(pkg) == norm:
            return url
    for top, (pkg, _) in KNOWN.items():  # an import name such as sklearn
        if top == package or normalise(pkg) == norm:
            return DOCS.get(pkg) or f"https://pypi.org/project/{pkg}/"
    return f"https://pypi.org/project/{package}/"


def purpose(package_or_import: str) -> str:
    hit = KNOWN.get(package_or_import)
    if hit:
        return hit[1]
    norm = normalise(package_or_import)
    for pkg, why in KNOWN.values():
        if normalise(pkg) == norm:
            return why
    return ""


class Classifier:
    """Built once per page or export: knows every catalogued module and every declared package."""

    def __init__(self, cat: Catalogue):
        self.modules_by_source: dict[str, set[str]] = defaultdict(set)
        self.roles: dict[str, str] = {}
        self.declared: dict[str, set[str]] = defaultdict(set)  # normalised package -> projects declaring it
        self.declared_names: dict[str, str] = {}
        for r in cat.conn.execute("SELECT source, kind, attrs FROM assets WHERE status = 'active' "
                                  "AND kind IN ('script', 'notebook', 'project')"):
            attrs = json.loads(r["attrs"])
            if r["kind"] == "project":
                self.roles[r["source"]] = attrs.get("role", "project")
                for d in (attrs.get("environment") or {}).get("deps", []):
                    self.declared[normalise(d["name"])].add(r["source"])
                    self.declared_names[normalise(d["name"])] = d["name"]
            for m in attrs.get("modules", []):
                self.modules_by_source[r["source"]].add(m.split(".")[0])
        self._classified: dict[tuple[str, str | None], str] = {}

    def package(self, top: str) -> str:
        if top in KNOWN:
            return KNOWN[top][0]
        if top in ALIASES:
            return ALIASES[top]
        return package_for(top)

    def classify(self, top: str, source: str | None = None) -> str:
        key = (top, source)
        if key not in self._classified:
            self._classified[key] = self._classify(top, source)
        return self._classified[key]

    def _classify(self, top: str, source: str | None) -> str:
        top = top.split(".")[0]
        if not top:
            return OWN
        if source and top in self.modules_by_source.get(source, ()):
            return OWN
        if any(top in mods for s, mods in self.modules_by_source.items() if s != source):
            return HOME
        if top in _STDLIB:
            return STDLIB
        if top in KNOWN or top in ALIASES or normalise(self.package(top)) in self.declared \
                or normalise(top) in self.declared:
            return THIRD
        from .codemap import _locate
        _, how = _locate(top, [])
        if how == "installed package":
            return THIRD
        return UNKNOWN


def packages_used(cat: Catalogue, classifier: Classifier | None = None, *, include_stdlib: bool = False
                  ) -> list[dict[str, Any]]:
    """One row per third-party (and optionally standard library) package your code imports:
    what it's for, how many files import it, from which projects, and the versions declared."""
    clf = classifier or Classifier(cat)
    rows: dict[str, dict[str, Any]] = {}
    for r in cat.conn.execute("SELECT key, source, attrs FROM assets WHERE kind IN ('script', 'notebook') "
                              "AND status = 'active'"):
        attrs = json.loads(r["attrs"])
        for imp in attrs.get("import_details", []):
            if imp.get("level"):
                continue
            top = imp["module"].split(".")[0]
            cls = clf.classify(top, r["source"])
            if cls != THIRD and not (include_stdlib and cls == STDLIB):
                continue
            pkg = clf.package(top) if cls == THIRD else top
            row = rows.setdefault(normalise(pkg), {"package": pkg, "kind": cls, "purpose": purpose(top) or purpose(pkg),
                                                    "imports": set(), "files": set(), "projects": set()})
            row["imports"].add(top)
            row["files"].add(r["key"])
            row["projects"].add(r["source"])
    out = []
    for norm, row in sorted(rows.items()):
        out.append({"package": row["package"], "kind": row["kind"], "purpose": row["purpose"],
                    "import as": ", ".join(sorted(row["imports"])), "files": len(row["files"]),
                    "projects": ", ".join(sorted(row["projects"])),
                    "declared in": ", ".join(sorted(clf.declared.get(norm, ()))),
                    "key": f"pkg://{norm}"})
    return out


def package_detail(cat: Catalogue, package: str) -> dict[str, Any]:
    """Everything about one package: who imports it, which names they use, declared versions."""
    clf = Classifier(cat)
    norm = normalise(package.removeprefix("pkg://"))
    files = []
    names: dict[str, set[str]] = defaultdict(set)
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind IN ('script', 'notebook') "
                              "AND status = 'active' ORDER BY source, name"):
        attrs = json.loads(r["attrs"])
        hits = [imp for imp in attrs.get("import_details", []) if not imp.get("level")
                and normalise(clf.package(imp["module"].split(".")[0])) == norm]
        if hits:
            used = sorted({n for imp in hits for n in imp.get("names", [])} | {imp["module"] for imp in hits
                                                                                  if not imp.get("names")})
            files.append({"key": r["key"], "file": r["name"], "project": r["source"], "uses": ", ".join(used)})
            for u in used:
                names[u].add(r["key"])
    versions = []
    for source in sorted(clf.declared.get(norm, ())):
        env = project_environment(cat, source)
        versions += [{"project": source, "version": d["version"] or "any", "file": d["file"]}
                     for d in env["deps"] if normalise(d["name"]) == norm]
    shown = clf.declared_names.get(norm) or next((p for p, _ in KNOWN.values() if normalise(p) == norm), norm)
    return {"package": shown, "key": f"pkg://{norm}", "purpose": purpose(shown), "files": files,
            "names": sorted(((n, len(k)) for n, k in names.items()), key=lambda x: (-x[1], x[0])),
            "versions": versions}
