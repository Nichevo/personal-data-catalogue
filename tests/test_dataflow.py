"""Version 2.13: where data comes from (APIs, scraping, databases...) and the data flow diagram."""
import json
import textwrap

import pytest

from pdcat.codemap import to_dot, to_mermaid
from pdcat.config import PythonSource
from pdcat.dataflow import flow_elements, flow_graph, source_rows, summary
from pdcat.datasources import office_sources, parse_connection, web_address
from pdcat.scan_python import scan_project, scan_python_source

API_PY = '''
"""Pull the weather forecast and store it next to sales."""
import requests as rq
import pandas as pd

BASE = "https://api.weather.example.com/v2"

def fetch(city, engine):
    r = rq.get(f"{BASE}/forecast?city={city}&key=SECRET", timeout=30)
    df = pd.DataFrame(r.json())
    df.to_sql("WeatherDaily", engine, schema="ml", if_exists="replace")
'''


def sources_of(code: str) -> dict[str, dict]:
    found = scan_python_source(textwrap.dedent(code))
    return {s["name"]: s for s in found.attrs.get("data_sources", [])}


def test_apis_are_found_with_their_host_but_not_the_query_string():
    got = sources_of('''
        import requests, httpx
        from urllib.request import urlopen, Request
        BASE = "https://api.weather.example.com/v2"
        def get(city):
            return requests.get(f"{BASE}/forecast?city={city}&apikey=SECRET").json()
        requests.put("https://hooks.example.com/upload", json={})
        client = httpx.Client(base_url="https://erp.example.com/odata")
        orders = client.get("/Orders")
        with requests.Session() as s:
            s.post("https://auth.example.com/token", data={"user": "me", "password": "pw"})
        req = Request("https://legacy.example.com/feed.xml")
        urlopen(req).read()
        requests.get(url_from_somewhere)
    ''')
    weather = got["api.weather.example.com"]
    assert weather["type"] == "api" and weather["function"] == "get"
    assert weather["addresses"] == ["https://api.weather.example.com/v2/forecast"]
    assert "SECRET" not in json.dumps(got)
    assert got["hooks.example.com"]["relation"] == "writes"
    assert got["erp.example.com"]["addresses"] == ["https://erp.example.com/odata/Orders"]
    assert got["auth.example.com"]["relation"] == "reads"
    assert "legacy.example.com" in got
    assert any(name.startswith("web address in url_from_somewhere") for name in got)


def test_web_scraping_is_told_apart_from_apis():
    got = sources_of('''
        import requests
        import pandas as pd
        from bs4 import BeautifulSoup
        from selenium import webdriver
        import scrapy
        page = requests.get("https://www.example-shop.com/products").text
        prices = requests.get("https://api.example-shop.com/v1/prices").json()
        soup = BeautifulSoup(page, "html.parser")
        driver = webdriver.Chrome()
        driver.get("https://portal.example.com/report")
        tables = pd.read_html("https://en.wikipedia.org/wiki/List")
        local = pd.read_html("saved/page.html")
        class Quotes(scrapy.Spider):
            start_urls = ["https://quotes.example.com/page/1"]
    ''')
    for host in ("www.example-shop.com", "portal.example.com", "en.wikipedia.org", "quotes.example.com"):
        assert got[host]["type"] == "web_scraping", host
    assert got["api.example-shop.com"]["type"] == "api"


def test_database_connections_keep_server_and_database_only():
    got = sources_of('''
        import pyodbc, sqlite3
        import sqlalchemy as sa
        from urllib.parse import quote_plus
        SERVER = "DEMO-SQL01"
        conn = pyodbc.connect(f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={SERVER};DATABASE=Forecast;UID=me;PWD=pw")
        e1 = sa.create_engine("mssql+pyodbc:///?odbc_connect=" + quote_plus(
            "DRIVER={SQL Server};SERVER=tcp:DEMO-SQL02,1433;DATABASE=Sales;Trusted_Connection=yes"))
        e2 = sa.create_engine("postgresql://bob:secret@pg.example.com:5432/analytics")
        e3 = sa.create_engine(get_url())
        local = sqlite3.connect("cache/local.db")
        memory = sqlite3.connect(":memory:")
    ''')
    assert got["DEMO-SQL01/Forecast"]["engine"] == "SQL Server"
    assert got["DEMO-SQL02/Sales"]["type"] == "database"
    assert got["pg.example.com/analytics"]["engine"] == "PostgreSQL"
    assert "cache/local.db" in got
    assert not any(":memory:" in n for n in got)
    assert any(n.startswith("database connection") or "connection (details" in n for n in got)
    text = json.dumps(got)
    assert "secret" not in text and "bob" not in text and "UID" not in text


@pytest.mark.parametrize("text, want", [
    ("Driver={ODBC Driver 18 for SQL Server};Server=tcp:sql1.example.com,1433;Database=Ops;Encrypt=yes",
     {"server": "sql1.example.com", "database": "Ops", "engine": "SQL Server"}),
    ("Data Source=SQL2;Initial Catalog=Plan;Integrated Security=SSPI", {"server": "SQL2", "database": "Plan"}),
    ("DSN=Warehouse;UID=x;PWD=y", {"dsn": "Warehouse"}),
    ("host=pg1 dbname=sales user=me password=pw", {"server": "pg1", "database": "sales"}),
    ("jdbc:sqlserver://sql3:1433;databaseName=Hist", {"server": "sql3", "database": "Hist"}),
    ("oracle+oracledb://u:p@ora1:1521/?service_name=ORCL", {"server": "ora1", "engine": "Oracle"}),
    ("hana://user:pw@hana.example.com:30015", {"server": "hana.example.com", "engine": "SAP HANA"}),
])
def test_connection_strings(text, want):
    got = parse_connection(text)
    for k, v in want.items():
        assert got.get(k) == v, (text, got)
    assert "pw" not in json.dumps(got) and "PWD" not in json.dumps(got)


def test_sap_cloud_and_web_files():
    found = scan_python_source(textwrap.dedent('''
        import boto3
        import pandas as pd
        from pyrfc import Connection
        sap = Connection(ashost="sap-host.example", sysnr="00", client="100", user="u", passwd="p")
        rows = sap.call("RFC_READ_TABLE", QUERY_TABLE="MARA")
        s3 = boto3.client("s3")
        prices = pd.read_csv("https://data.example.org/files/prices.csv?token=abc")
        raw = pd.read_parquet("s3://my-bucket/raw/x.parquet")
        local = pd.read_csv("data/local.csv")
    '''))
    got = {s["name"]: s for s in found.attrs["data_sources"]}
    assert got["sap-host.example"]["type"] == "sap"
    assert got["sap-host.example"]["calls"] == ["RFC_READ_TABLE MARA"]
    assert got["sap-host.example"]["detail"] == "client 100"
    assert got["Amazon S3"]["type"] == "cloud_storage"
    assert got["s3://my-bucket"]["type"] == "cloud_storage"
    assert got["data.example.org"]["type"] == "web_file"
    assert got["data.example.org"]["addresses"] == ["https://data.example.org/files/prices.csv"]
    # the web and cloud paths are no longer listed as local files; the local one still is
    assert [r for _, r, _ in found.refs] == ["file:data/local.csv"]


def test_web_address():
    assert web_address("https://user:pw@Host.Example.com:8443/a/b?key=1") == ("https://host.example.com/a/b",
                                                                               "host.example.com")
    assert web_address("{}/orders") == (None, None)
    assert web_address(None) == (None, None)


def test_bad_code_does_not_break_the_scan():
    found = scan_python_source("import requests\nrequests.get(\n")
    assert "parse_error" in found.attrs
    found = scan_python_source("import requests\nrequests.get()\nrequests.request('GET')\nx = {}.get('a')\n")
    assert not found.attrs.get("parse_error")


def test_notebook_sources(tmp_path, catalogue):
    nb = {"cells": [{"cell_type": "code", "source": ["import requests\n",
                                                     "r = requests.get('https://api.example.com/v1/items')\n"]}]}
    root = tmp_path / "nbproj"
    root.mkdir()
    (root / "pull.ipynb").write_text(json.dumps(nb))
    scan_project(catalogue, PythonSource(name="nbproj", path=root))
    a = catalogue.get_asset("ext://nbproj/api/api.example.com")
    assert a and a["kind"] == "data_source"


@pytest.fixture
def flows(tmp_path, catalogue):
    """forecasting writes ml.ForecastOutput; weather reads an API and writes ml.WeatherDaily;
    reporting reads both tables."""
    weather = tmp_path / "weather"
    weather.mkdir()
    (weather / "pull.py").write_text(textwrap.dedent(API_PY))
    scan_project(catalogue, PythonSource(name="weather", path=weather, default_database="warehouse/ForecastDB"))
    reporting = tmp_path / "reporting"
    reporting.mkdir()
    (reporting / "report.py").write_text(textwrap.dedent('''
        import pandas as pd
        def build(engine):
            a = pd.read_sql("SELECT * FROM ml.ForecastOutput f JOIN ml.WeatherDaily w ON w.d = f.d", engine)
            a.to_excel("out/forecast_vs_weather.xlsx")
    '''))
    scan_project(catalogue, PythonSource(name="reporting", path=reporting, default_database="warehouse/ForecastDB"))
    return catalogue


def test_sources_are_saved_and_linked(flows):
    key = "ext://weather/api/api.weather.example.com"
    a = flows.get_asset(key)
    assert a["kind"] == "data_source" and a["attrs"]["source_type"] == "api"
    assert a["attrs"]["addresses"] == ["https://api.weather.example.com/v2/forecast"]
    users = flows.conn.execute("SELECT src_key, how, evidence FROM links WHERE dst_key = ?", (key,)).fetchall()
    assert users and users[0]["src_key"] == "py://weather/pull.py"
    rows = source_rows(flows)
    assert rows[0]["source"] == "api.weather.example.com" and rows[0]["used by"] == "pull.py"
    assert "fetch()" in rows[0]["how found"]


def test_rescan_drops_a_source_the_code_no_longer_uses(flows, tmp_path):
    (tmp_path / "weather" / "pull.py").write_text("x = 1\n")
    scan_project(flows, PythonSource(name="weather", path=tmp_path / "weather"))
    assert flows.get_asset("ext://weather/api/api.weather.example.com")["status"] == "missing"
    assert source_rows(flows) == []


def test_flow_follows_data_from_the_api_to_the_output(flows):
    g = flow_graph(flows, "weather")
    api = "ext:api:api.weather.example.com"
    assert g.nodes[api]["role"] == "source" and g.nodes[api]["group"].startswith("🌐")
    edges = set(g.edges)
    assert (api, "py://weather/pull.py", "reads") in edges
    weather_table = next(b for (a, b, r) in edges if a == "py://weather/pull.py" and r == "writes")
    # downstream: the reporting project reads what weather writes, and writes a workbook
    assert (weather_table, "py://reporting/report.py", "reads") in edges
    assert any(a == "py://reporting/report.py" and b.endswith("forecast_vs_weather.xlsx") for a, b, _ in edges)
    assert g.nodes[next(b for a, b, _ in edges if b.endswith(".xlsx"))]["role"] == "output"
    # the forecasting project writes ForecastOutput, which reporting reads: upstream of reporting only
    assert "py://forecasting/src/features.py" not in g.nodes
    assert "py://forecasting/src/features.py" in flow_graph(flows, "reporting").nodes
    assert summary(g)["api"] == 1


def test_flow_for_everything_and_by_project(flows):
    g = flow_graph(flows)
    assert {"py://weather/pull.py", "py://reporting/report.py", "py://forecasting/src/features.py"} <= set(g.nodes)
    # tables nothing writes are sources, grouped by database
    sales = "sql://warehouse/forecastdb/dbo.salesdaily"
    assert g.nodes[sales]["role"] == "source" and g.nodes[sales]["group"] == "🛢️ ForecastDB"
    p = flow_graph(flows, level="projects")
    assert "src:weather" in p.nodes and "py://weather/pull.py" not in p.nodes
    elements = flow_elements(g)
    ids = {e["data"]["id"] for e in elements}
    assert all(e["data"]["source"] in ids and e["data"]["target"] in ids for e in elements if "source" in e["data"])
    groups = [e for e in elements if e["data"]["id"].startswith("grp:")]
    assert all(e["data"]["count"] > 0 for e in groups)


def test_flow_exports(flows):
    g = flow_graph(flows)
    dot, mermaid = to_dot(g), to_mermaid(g)
    assert "hexagon" in dot and "api.weather.example.com" in dot
    assert "classDef api" in mermaid and "api.weather.example.com" in mermaid


def test_same_api_in_two_projects_is_one_box(flows, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.py").write_text(textwrap.dedent(API_PY).replace("WeatherDaily", "WeatherCopy"))
    scan_project(flows, PythonSource(name="other", path=other))
    g = flow_graph(flows)
    assert len([n for n in g.nodes if n.startswith("ext:api:api.weather")]) == 1
    assert len([1 for (a, _, _) in g.edges if a == "ext:api:api.weather.example.com"]) == 2


def test_power_query_sources(tmp_path, catalogue):
    from test_office import write_pbit

    from pdcat.scan_files import scan_folder
    folder = tmp_path / "reports"
    folder.mkdir()
    write_pbit(folder / "Sales.pbit")
    scan_folder(catalogue, "reports", folder)
    rows = [r for r in source_rows(catalogue) if r["project or folder"] == "reports"]
    assert any(r["source"] == "DEMO-SQL01/ForecastDB" and r["type"] == "Database connection" for r in rows)
    got = office_sources([{"name": "Feed", "web": ["https://contoso.sharepoint.com/sites/x/Shared Documents"]},
                          {"name": "Api", "web": ["https://api.example.com/v1/rates?key=1"]}],
                         [{"type": "ODBC", "name": "c", "dsn": "Warehouse", "connection": "DSN=Warehouse"}])
    kinds = {s["name"]: s["type"] for s in got}
    assert kinds == {"contoso.sharepoint.com": "sharepoint", "api.example.com": "api", "DSN Warehouse": "database"}
    g = flow_graph(catalogue, "reports")
    assert any(n["kind"] == "database" for n in g.nodes.values())


def test_data_flow_page_and_source_page_render(flows, monkeypatch):
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    from test_app import APP, goto
    monkeypatch.setenv("PDCAT_DB", str(flows.path))
    app = AppTest.from_file(APP, default_timeout=15)
    app.run()
    goto(app, "Data flow")
    assert not app.exception
    assert any("Where data comes from" in s.value for s in app.subheader)
    app.selectbox(key="flow-start").set_value("weather").run()
    assert not app.exception
    app.radio(key="flow-level").set_value("projects").run()
    assert not app.exception
    for key in ("ext://weather/api/api.weather.example.com", "py://weather"):
        app.query_params["asset"] = key
        app.run()
        assert not app.exception, (key, app.exception)
