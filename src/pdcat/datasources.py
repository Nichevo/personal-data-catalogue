"""Where data comes from outside your own files and tables: APIs, web scraping, database
connections, cloud storage, SharePoint and SAP.

Python code is read for the calls that fetch data (requests, httpx, aiohttp, urllib,
BeautifulSoup with requests, Selenium, Playwright, Scrapy, pyodbc, SQLAlchemy, pyrfc and
friends), and Power Query in reports and workbooks for Web.Contents, SharePoint, OData and
the servers it connects to. Each source becomes a "data_source" item with its type and,
when the code spells it out, its address: the host and path of a web address (never the
query string, a user name or a password) or the server and database of a connection.
The code that uses it gets a "reads" link (or "writes" for PUT, PATCH and DELETE).

The data flow page (dataflow.py) draws these on the left of an ETL-style diagram.
"""
from __future__ import annotations

import ast
import re
from typing import Any
from urllib.parse import unquote_plus, urlsplit

# type -> (icon, label)
SOURCE_TYPES = {
    "api": ("🌐", "API or web service"),
    "web_scraping": ("🕸️", "Web scraping"),
    "web_file": ("⬇️", "File from the web"),
    "database": ("🛢️", "Database connection"),
    "cloud_storage": ("☁️", "Cloud storage"),
    "sharepoint": ("📎", "SharePoint"),
    "sap": ("🏭", "SAP system"),
}

HTTP_MODULES = {"requests", "httpx", "urllib3", "requests_cache", "niquests", "grequests", "treq"}
HTTP_VERBS = {"get": "reads", "post": "reads", "head": "reads", "options": "reads", "request": "reads",
              "stream": "reads", "put": "writes", "patch": "writes", "delete": "writes"}
# Calls that make an object which then fetches web pages: what kind it is.
CLIENTS = {"requests.Session": "http", "requests.session": "http", "httpx.Client": "http",
           "httpx.AsyncClient": "http", "aiohttp.ClientSession": "http", "urllib3.PoolManager": "http",
           "requests_cache.CachedSession": "http", "niquests.Session": "http",
           "requests_html.HTMLSession": "scrape", "requests_html.AsyncHTMLSession": "scrape",
           "cloudscraper.create_scraper": "scrape", "cloudscraper.CloudScraper": "scrape",
           "mechanicalsoup.StatefulBrowser": "scrape", "mechanicalsoup.Browser": "scrape",
           "undetected_chromedriver.Chrome": "browser"}
# Importing one of these means the web pages fetched in that file are being scraped.
SCRAPING_LIBS = {"bs4", "BeautifulSoup", "lxml", "parsel", "requests_html", "mechanicalsoup", "selectolax",
                 "pyquery", "scrapy", "selenium", "playwright", "pyppeteer", "cloudscraper", "html5lib",
                 "undetected_chromedriver", "splinter", "trafilatura", "newspaper"}
DB_CONNECTS = {
    "pyodbc.connect": "ODBC", "turbodbc.connect": "ODBC", "pypyodbc.connect": "ODBC",
    "pymssql.connect": "SQL Server", "pytds.connect": "SQL Server", "mssql_python.connect": "SQL Server",
    "sqlalchemy.create_engine": "", "sqlalchemy.engine.create_engine": "",
    "sqlalchemy.ext.asyncio.create_async_engine": "", "sqlalchemy.URL.create": "",
    "sqlalchemy.engine.URL.create": "", "sqlalchemy.engine.url.URL.create": "",
    "psycopg2.connect": "PostgreSQL", "psycopg.connect": "PostgreSQL", "pg8000.connect": "PostgreSQL",
    "asyncpg.connect": "PostgreSQL", "pymysql.connect": "MySQL", "mysql.connector.connect": "MySQL",
    "MySQLdb.connect": "MySQL", "mariadb.connect": "MariaDB", "cx_Oracle.connect": "Oracle",
    "oracledb.connect": "Oracle", "sqlite3.connect": "SQLite", "duckdb.connect": "DuckDB",
    "snowflake.connector.connect": "Snowflake", "hdbcli.dbapi.connect": "SAP HANA",
    "teradatasql.connect": "Teradata", "databricks.sql.connect": "Databricks", "pyhive.hive.connect": "Hive",
    "ibm_db.connect": "Db2", "ibm_db_dbi.connect": "Db2", "jaydebeapi.connect": "JDBC",
    "adodbapi.connect": "ADO", "pymongo.MongoClient": "MongoDB", "redis.Redis": "Redis",
    "redis.StrictRedis": "Redis", "google.cloud.bigquery.Client": "BigQuery", "pandas_gbq.read_gbq": "BigQuery",
    "clickhouse_connect.get_client": "ClickHouse", "trino.dbapi.connect": "Trino",
}
URL_ENGINES = {"mssql": "SQL Server", "sqlserver": "SQL Server", "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
               "mysql": "MySQL", "mariadb": "MariaDB", "oracle": "Oracle", "sqlite": "SQLite", "duckdb": "DuckDB",
               "snowflake": "Snowflake", "hana": "SAP HANA", "sap": "SAP HANA", "databricks": "Databricks",
               "bigquery": "BigQuery", "teradata": "Teradata", "redshift": "Redshift", "db2": "Db2",
               "ibm_db_sa": "Db2", "mongodb": "MongoDB", "redis": "Redis", "hive": "Hive", "trino": "Trino",
               "access": "Access"}
CLOUD_SCHEMES = {"s3": "Amazon S3", "s3a": "Amazon S3", "gs": "Google Cloud Storage", "gcs": "Google Cloud Storage",
                 "abfs": "Azure Data Lake", "abfss": "Azure Data Lake", "adl": "Azure Data Lake",
                 "az": "Azure Blob Storage", "wasb": "Azure Blob Storage", "wasbs": "Azure Blob Storage"}
CLOUD_CLIENTS = {"azure.storage.blob.BlobServiceClient": "Azure Blob Storage",
                 "azure.storage.blob.ContainerClient": "Azure Blob Storage",
                 "azure.storage.blob.BlobClient": "Azure Blob Storage",
                 "azure.storage.filedatalake.DataLakeServiceClient": "Azure Data Lake",
                 "google.cloud.storage.Client": "Google Cloud Storage"}
SHAREPOINT_CLIENTS = {"office365.sharepoint.client_context.ClientContext", "shareplum.Site",
                      "shareplum.Office365"}
MAX_ADDRESSES = 10


# ---- Python code -----------------------------------------------------------------

def find_sources(tree: ast.Module, env) -> list[dict[str, Any]]:
    """The outside sources one file's code fetches data from, each as {"type", "name", "relation",
    "line", "how", "function", "addresses", "engine", "detail"}. `env` is scan_python's Env."""
    from .scan_python import _call_name, _src
    imports = getattr(env, "imports", {}) or {}
    top_imports = {v.split(".")[0] for v in imports.values()} | set(imports)
    scraping = bool(top_imports & SCRAPING_LIBS)
    browser_page = bool(top_imports & {"playwright", "pyppeteer"})
    clients = _clients(tree, imports)
    out: list[dict[str, Any]] = []

    def add(kind: str, name: str, call: ast.AST, how: str, relation: str = "reads", address: str | None = None,
            **extra: Any) -> None:
        out.append({"type": kind, "name": name, "relation": relation, "line": getattr(call, "lineno", 0),
                    "how": how, "function": env.enclosing.get(id(call)) if hasattr(env, "enclosing") else None,
                    "addresses": [address] if address else [], **{k: v for k, v in extra.items() if v}})

    def web(call: ast.Call, node: ast.AST | None, how: str, relation: str = "reads", base: str | None = None,
            scraped: bool = False, kind: str | None = None) -> None:
        if node is None:
            return
        value = _value(node, env, call)
        if value and base and not re.match(r"^[a-z][\w+.-]*://", value, re.I):
            value = base.rstrip("/") + "/" + value.lstrip("/")
        address, host = web_address(value)
        if not host:
            add(kind or ("web_scraping" if scraped else "api"), f"web address in {_src(node, 50)}", call, how,
                relation, expression=_src(node, 200))
            return
        add(kind or web_kind(address, scraped), host, call, how, relation, address)

    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        full = _full(call, imports, _call_name)
        func = call.func
        attr = func.attr if isinstance(func, ast.Attribute) else None
        if hasattr(env, "at"):
            env.at = call
        # requests.get(url), session.post(url), httpx.Client(base_url=...).get("/x"), aiohttp session.get(url)
        if attr in HTTP_VERBS or (attr in ("open", "get") and isinstance(func, ast.Attribute)):
            owner = func.value
            owner_full = _full(owner, imports, _call_name) if isinstance(owner, ast.Call) else None
            owner_name = imports.get(owner.id, owner.id) if isinstance(owner, ast.Name) else None
            client = clients.get(_src(owner, 200)) or (
                {"kind": CLIENTS[owner_full], "base": _keyword_value(owner, "base_url", env, call)}
                if owner_full in CLIENTS else None)
            if client and client["kind"] == "browser" and attr == "get":
                web(call, _arg(call, 0, "url"), f"browser.get ({_src(owner, 30)})", scraped=True)
                continue
            if client and client["kind"] == "scrape" and attr in ("get", "post", "open"):
                web(call, _arg(call, 0, "url"), f"{_src(owner, 30)}.{attr}", scraped=True)
                continue
            if attr in HTTP_VERBS and (owner_name in HTTP_MODULES or (client and client["kind"] == "http")):
                node = _arg(call, 1, "url") if attr == "request" else _arg(call, 0, "url")
                relation = HTTP_VERBS[attr]
                if attr == "request":
                    method = _value(_arg(call, 0, "method"), env, call) or ""
                    relation = HTTP_VERBS.get(method.lower(), "reads")
                lib = owner_name or (owner_full or "").split(".")[0] or _src(owner, 30)
                web(call, node, f"{lib}.{attr}" + (" (POST)" if attr == "post" else ""), relation,
                    base=client.get("base") if client else None, scraped=scraping and relation == "reads")
                continue
        if full in ("urllib.request.urlopen", "urllib2.urlopen", "urllib.urlopen"):
            first = _arg(call, 0, "url")
            target = env.value_node(first.id) if isinstance(first, ast.Name) and hasattr(env, "value_node") else first
            if isinstance(target, ast.Call) and (_full(target, imports, _call_name) or "").endswith("Request"):
                continue  # the Request(...) call is counted on its own
            web(call, first, "urllib urlopen", scraped=scraping)
        elif full in ("urllib.request.Request", "urllib2.Request"):
            web(call, _arg(call, 0, "url"), "urllib Request", scraped=scraping)
        elif full in ("urllib.request.urlretrieve", "urllib.urlretrieve", "wget.download", "gdown.download"):
            web(call, _arg(call, 0, "url"), full, kind="web_file")
        elif full in ("pandas.read_html",) or (attr == "read_html" and call.args):
            value = _value(_arg(call, 0, "io"), env, call)
            if value and "://" in value:
                web(call, _arg(call, 0, "io"), "pandas read_html", scraped=True)
        elif browser_page and attr == "goto":
            web(call, _arg(call, 0, "url"), f"{_src(func.value, 30)}.goto", scraped=True)
        elif full and (full in DB_CONNECTS or full.endswith(".jdbc") or attr == "jdbc"):
            _database(call, full, env, add)
        elif full in ("pyrfc.Connection", "pyrfc.Server"):
            host = (_keyword_value(call, "ashost", env, call) or _keyword_value(call, "mshost", env, call)
                    or _keyword_value(call, "dest", env, call))
            system = _keyword_value(call, "sysid", env, call) or _keyword_value(call, "r3name", env, call)
            client_no = _keyword_value(call, "client", env, call)
            name = " ".join(p for p in (system, host) if p) or "SAP system (details from settings)"
            add("sap", name, call, "pyrfc.Connection", engine="SAP RFC",
                detail=f"client {client_no}" if client_no else None, calls=_rfc_calls(tree, env))
        elif full in CLOUD_CLIENTS or (full or "").endswith(("BlobServiceClient.from_connection_string",
                                                              "ContainerClient.from_connection_string")):
            label = CLOUD_CLIENTS.get(full) or "Azure Blob Storage"
            text = " ".join(v for a in [*call.args, *(k.value for k in call.keywords)]
                            if (v := _value(a, env, call)))
            m = re.search(r"(?i)AccountName=([^;]+)", text) or re.search(r"https?://([\w-]+)\.(?:blob|dfs)\.", text)
            add("cloud_storage", f"{label} {m.group(1)}" if m else label, call, full.rsplit(".", 1)[-1],
                engine=label)
        elif full in ("boto3.client", "boto3.resource", "boto3.session.Session.client") or \
                (attr in ("client", "resource") and isinstance(func.value, ast.Call)
                 and (_full(func.value, imports, _call_name) or "").startswith("boto3")):
            service = _value(_arg(call, 0, "service_name"), env, call) or "?"
            if service == "s3":
                add("cloud_storage", "Amazon S3", call, f"boto3 {service}", engine="Amazon S3")
            else:
                add("api", f"AWS {service}", call, f"boto3 {service}")
        elif full in SHAREPOINT_CLIENTS:
            value = _value(_arg(call, 0, "base_url", "site_url", "url"), env, call)
            address, host = web_address(value)
            add("sharepoint", host or "SharePoint", call, full.rsplit(".", 1)[-1], address=address)

    # Scrapy spiders: start_urls = [...] on the class
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        if not any("Spider" in _src(b, 80) for b in cls.bases):
            continue
        for stmt in cls.body:
            if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "start_urls"
                                                     for t in stmt.targets) and isinstance(stmt.value, (ast.List, ast.Tuple)):
                for elt in stmt.value.elts:
                    address, host = web_address(_value(elt, env, stmt))
                    if host:
                        add("web_scraping", host, stmt, f"Scrapy spider {cls.name}", address=address)
    return merge(out)


def from_file_refs(refs: list[tuple[str, str, int]]) -> tuple[list[tuple[str, str, int]], list[dict[str, Any]]]:
    """Split "file:" references that are really web addresses (pd.read_csv("https://...")) or cloud
    storage (s3://bucket/x.parquet) off into sources. Returns (the other refs, the sources)."""
    keep, sources = [], []
    for relation, ref, line in refs:
        path = ref[5:] if ref.startswith("file:") else ""
        m = re.match(r"^([a-z][\w+.-]*)://", path, re.I)
        if not m:
            keep.append((relation, ref, line))
            continue
        scheme = m.group(1).lower()
        if scheme in CLOUD_SCHEMES:
            bucket = urlsplit(path).netloc.split("@")[-1] or "?"
            sources.append({"type": "cloud_storage", "name": f"{scheme}://{bucket}", "relation": relation,
                            "line": line, "how": "file path in cloud storage", "addresses": [path.split("?")[0]],
                            "engine": CLOUD_SCHEMES[scheme]})
        elif scheme in ("http", "https", "ftp", "ftps", "sftp"):
            address, host = web_address(path)
            kind = "sharepoint" if host and "sharepoint" in host else "web_file"
            sources.append({"type": kind, "name": host or path, "relation": relation, "line": line,
                            "how": "file read from a web address", "addresses": [address] if address else []})
        else:
            keep.append((relation, ref, line))
    return keep, merge(sources)


def _clients(tree: ast.Module, imports: dict[str, str]) -> dict[str, dict]:
    """Variables holding an HTTP session, scraper or browser: {"session": {"kind": "http", "base": url}}."""
    from .scan_python import _call_name, _src
    out: dict[str, dict] = {}

    def note(target: ast.AST | None, value: ast.AST) -> None:
        if target is None or not isinstance(value, ast.Call):
            return
        full = _full(value, imports, _call_name) or ""
        kind = CLIENTS.get(full) or ("browser" if full.startswith(("selenium.webdriver.", "seleniumwire.webdriver."))
                                     and full.rsplit(".", 1)[-1][:1].isupper() else None)
        if kind:
            base = next((k.value.value for k in value.keywords if k.arg == "base_url"
                         and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str)), None)
            out[_src(target, 200)] = {"kind": kind, "base": base}

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                note(t, node.value)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            note(node.target, node.value)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                note(item.optional_vars, item.context_expr)
    return out


def _database(call: ast.Call, full: str, env, add) -> None:
    from .scan_python import _src
    engine = DB_CONNECTS.get(full, "JDBC") if not full.endswith(".jdbc") else "JDBC"
    pieces: dict[str, str] = {}
    texts = []
    for a in [*call.args, *(k.value for k in call.keywords if k.arg not in ("user", "password", "pwd", "uid"))]:
        for node in ast.walk(a):  # also the string inside quote_plus(...) and other wrappers
            if isinstance(node, (ast.Constant, ast.JoinedStr, ast.Name, ast.BinOp, ast.Attribute, ast.Subscript)):
                v = _value(node, env, call)
                if v and v not in texts:
                    texts.append(v)
    for text in texts:
        got = parse_connection(text)
        for k, v in got.items():
            pieces.setdefault(k, v)
    for kw in call.keywords:
        if kw.arg in ("server", "host", "address", "hostname", "dsn", "database", "dbname", "db",
                      "account", "server_hostname", "drivername"):
            v = _value(kw.value, env, call)
            if v:
                key = {"host": "server", "address": "server", "hostname": "server", "server_hostname": "server",
                       "account": "server", "dbname": "database", "db": "database", "drivername": "scheme"}.get(kw.arg,
                                                                                                         kw.arg)
                pieces.setdefault(key, v)
    if pieces.get("scheme"):
        engine = URL_ENGINES.get(re.split(r"[+:]", pieces["scheme"])[0].lower(), engine) or engine
    engine = pieces.get("engine") or engine or "database"
    if engine in ("SQLite", "DuckDB") and not pieces.get("server"):
        path = texts[0] if texts else None
        if not path or path == ":memory:":
            return
        pieces["database"] = pieces.get("database") or path
    name = connection_name(pieces, engine)
    how = full.rsplit(".", 2)[-2] + "." + full.rsplit(".", 1)[-1] if "." in full else full
    add("database", name or f"{engine} connection (details from {_src(call.args[0], 40) if call.args else 'settings'})",
        call, how, engine=engine, detail=f"DSN {pieces['dsn']}" if pieces.get("dsn") else None)


def parse_connection(text: str) -> dict[str, str]:
    """Server, database, DSN and engine from a connection string or database address, without the user
    name or password: "DRIVER={ODBC Driver 17 for SQL Server};SERVER=x;DATABASE=y",
    "mssql+pyodbc://user:pw@x/y?driver=...", "mssql+pyodbc:///?odbc_connect=...", "host=x dbname=y",
    "jdbc:sqlserver://x:1433;databaseName=y", "x:1521/service"."""
    text = (text or "").strip()
    out: dict[str, str] = {}
    if not text:
        return out
    if text.lower().startswith("jdbc:"):
        text = text[5:]
        m = re.match(r"(?i)oracle:thin:@(?://)?([^:/;]+)(?::\d+)?[:/]([\w.{}-]+)", text)
        if m:
            return {"scheme": "oracle", "server": m.group(1), "database": m.group(2), "engine": "Oracle"}
    m = re.match(r"^([a-z][\w+.-]*)://([^?#]*)(\?[^#]*)?", text, re.I)
    if m:
        scheme = m.group(1).lower()
        out["scheme"] = scheme
        engine = URL_ENGINES.get(re.split(r"[+:]", scheme)[0])
        if engine:
            out["engine"] = engine
        rest = m.group(2)
        hostpart, _, path = rest.partition("/")
        hostpart = hostpart.split(";")[0]
        host = hostpart.rsplit("@", 1)[-1]
        if host:
            out["server"] = re.sub(r":\d+$", "", host)
        db = path.split(";")[0].strip("/")
        if db:
            out["database"] = unquote_plus(db)
        jdbc_db = re.search(r"(?i);\s*(?:databaseName|database)\s*=\s*([^;]+)", rest)
        if jdbc_db:
            out["database"] = jdbc_db.group(1).strip()
        query = m.group(3) or ""
        odbc = re.search(r"(?i)odbc_connect=([^&]+)", query)
        if odbc:
            for k, v in parse_connection(unquote_plus(odbc.group(1))).items():
                out.setdefault(k, v)
        return out
    value = r"\s*=\s*(\{[^}]*\}|\"[^\"]*\"|'[^']*'|[^;]*?)\s*(?=;|\s+[\w ]+?\s*=|$)"
    for key, names in (("server", r"server|data source|address|addr|network address|host|hostname|ashost"),
                       ("database", r"database|initial catalog|dbname|db"), ("dsn", r"dsn"),
                       ("driver", r"driver|provider")):
        m = re.search(r"(?i)(?:^|[;\s])(?:" + names + r")" + value, text)
        if m and m.group(1).strip("{}\"' "):
            out[key] = m.group(1).strip("{}\"' ")
    if out.get("server"):
        out["server"] = re.sub(r"(?i)^tcp:", "", out["server"]).split(",")[0]
    driver = out.pop("driver", "").lower()
    if "sql server" in driver or "sqlncli" in driver or "sqloledb" in driver or "msoledbsql" in driver:
        out["engine"] = "SQL Server"
    elif "oracle" in driver:
        out["engine"] = "Oracle"
    elif "hana" in driver or "hdbodbc" in driver:
        out["engine"] = "SAP HANA"
    elif "postgres" in driver:
        out["engine"] = "PostgreSQL"
    elif "access" in driver or "ace.oledb" in driver or "jet.oledb" in driver:
        out["engine"] = "Access"
    elif "excel" in driver:
        out["engine"] = "Excel"
    if not out and re.fullmatch(r"[\w.{}-]+:\d+/[\w.{}-]+", text):  # oracle host:port/service
        host, _, svc = text.partition("/")
        out = {"server": host.split(":")[0], "database": svc}
    return out


def connection_name(pieces: dict[str, str], engine: str = "") -> str | None:
    server, db, dsn = pieces.get("server"), pieces.get("database"), pieces.get("dsn")
    if server and db:
        return f"{server}/{db}"
    if server:
        return server
    if dsn:
        return f"DSN {dsn}" + (f"/{db}" if db else "")
    if db:
        return f"{engine} {db}".strip() if engine not in ("SQLite", "DuckDB") else db
    return None


def web_address(value: str | None) -> tuple[str | None, str | None]:
    """("https://host/path", "host") from a web address, leaving out the query string (it often holds a key),
    any user name and password, and the port. (None, None) when there's no host to show."""
    if not value or "://" not in value:
        return None, None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None, None
    host = parts.netloc.rsplit("@", 1)[-1]
    host = re.sub(r":\d+$", "", host).lower()
    if not host or host.startswith("{}") or host == "{}":
        return None, None
    path = parts.path if len(parts.path) <= 200 else parts.path[:199] + "…"
    return f"{parts.scheme.lower()}://{host}{path}", host


def web_kind(address: str, scraped: bool) -> str:
    """api, sharepoint or web_scraping for a fetched web address."""
    lower = address.lower()
    host = urlsplit(lower).netloc
    if "sharepoint" in host:
        return "sharepoint"
    looks_api = bool(re.search(r"(^|[./])api[.-]|/api(/|$)|/v\d+(\.\d+)?(/|$)|graphql|/odata|/rest/|/services/"
                               r"|\.json$|/sap/opu/", lower)) or host.startswith("api")
    return "web_scraping" if scraped and not looks_api else "api"


def merge(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per (type, name, relation), keeping the first line and every address (up to ten)."""
    out: dict[tuple, dict] = {}
    for s in sources:
        k = (s["type"], s["name"].lower(), s["relation"])
        if k not in out:
            out[k] = {**s, "addresses": list(s.get("addresses", []))}
        else:
            have = out[k]["addresses"]
            have += [a for a in s.get("addresses", []) if a not in have][:MAX_ADDRESSES - len(have)]
    return list(out.values())


def _rfc_calls(tree: ast.Module, env) -> list[str]:
    """conn.call("RFC_READ_TABLE", QUERY_TABLE="MARA") -> ["RFC_READ_TABLE MARA"]."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "call" and n.args \
                and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str) \
                and re.fullmatch(r"[A-Z0-9_/]+", n.args[0].value):
            table = next((k.value.value for k in n.keywords if k.arg == "QUERY_TABLE"
                          and isinstance(k.value, ast.Constant)), None)
            text = n.args[0].value + (f" {table}" if table else "")
            if text not in out:
                out.append(text)
    return out[:50]


def _full(call: ast.Call, imports: dict[str, str], call_name) -> str | None:
    """The full dotted name of what is called, through import aliases: rq.get -> requests.get,
    urlopen -> urllib.request.urlopen, sa.create_engine -> sqlalchemy.create_engine."""
    dotted = call_name(call)
    if not dotted:
        return None
    head, _, rest = dotted.partition(".")
    base = imports.get(head)
    if base and not base.startswith("."):
        return base + ("." + rest if rest else "")
    return dotted


def _arg(call: ast.Call, pos: int, *kw: str) -> ast.AST | None:
    for k in call.keywords:
        if k.arg in kw:
            return k.value
    return call.args[pos] if len(call.args) > pos else None


def _value(node: ast.AST | None, env, at: ast.AST) -> str | None:
    if node is None:
        return None
    from .scan_python import _expr_value
    if hasattr(env, "at"):
        env.at = at
    try:
        return _expr_value(node, env)[0]
    except RecursionError:
        return None


def _keyword_value(call: ast.Call, name: str, env, at: ast.AST) -> str | None:
    node = next((k.value for k in call.keywords if k.arg == name), None)
    return _value(node, env, at) if node is not None else None


# ---- Power Query and workbook connections --------------------------------------

def office_sources(queries: list[dict[str, Any]], connections: list[dict[str, Any]] | None = None
                   ) -> list[dict[str, Any]]:
    """Sources in a report's or workbook's Power Query (Web.Contents, SharePoint, OData, Sql.Database,
    ODBC) and in a workbook's own connections."""
    out = []
    for q in queries:
        how = f"Power Query “{q.get('name', '')}”"
        for url in q.get("web", []):
            address, host = web_address(url)
            if host:
                out.append({"type": web_kind(address, False), "name": host, "relation": "reads", "line": 0,
                            "how": how, "addresses": [address]})
        dbs = q.get("databases", [])
        for server in q.get("servers", []):
            out.append({"type": "database", "name": f"{server}/{dbs[0]}" if len(dbs) == 1 else server,
                        "relation": "reads", "line": 0, "how": how, "engine": "SQL Server", "addresses": []})
        for odbc in q.get("odbc", []):
            pieces = parse_connection(odbc)
            name = connection_name(pieces, pieces.get("engine", "ODBC"))
            if name:
                out.append({"type": "database", "name": name, "relation": "reads", "line": 0, "how": how,
                            "engine": pieces.get("engine", "ODBC"), "addresses": []})
    for c in connections or []:
        if c.get("type") == "Power Query":
            continue
        how = f"{c.get('type') or 'workbook'} connection “{c.get('name', '')}”"
        if c.get("url"):
            address, host = web_address(c["url"])
            if host:
                out.append({"type": web_kind(address, True), "name": host, "relation": "reads", "line": 0,
                            "how": how, "addresses": [address]})
        pieces = {k: c[k] for k in ("server", "database", "dsn") if c.get(k)}
        if pieces:
            engine = parse_connection(c.get("connection", "")).get("engine", "")
            out.append({"type": "database", "name": connection_name(pieces, engine), "relation": "reads",
                        "line": 0, "how": how, "engine": engine, "addresses": []})
    return merge(out)


# ---- saving them -------------------------------------------------------------

def source_key(source: str, kind: str, name: str) -> str:
    return f"ext://{source}/{kind}/{name.lower()}"


def record_sources(cat, src_key: str, source: str, sources: list[dict[str, Any]] | None, *,
                   parent: str | None = None, evidence: str | None = None) -> int:
    """Save each source as a data_source item of this scan's source and link the code (or report) to it.
    Call inside `cat.scan(source)`: an item already seen in this scan keeps the addresses found before."""
    n = 0
    for s in sources or []:
        key = source_key(source, s["type"], s["name"])
        old = cat.conn.execute("SELECT attrs, status FROM assets WHERE key = ?", (key,)).fetchone()
        addresses = list(s.get("addresses", []))
        used_for: list[str] = list(s.get("calls", []))
        if old and old["status"] == "active":
            import json
            prev = json.loads(old["attrs"])
            addresses = list(dict.fromkeys(prev.get("addresses", []) + addresses))[:MAX_ADDRESSES]
            used_for = list(dict.fromkeys(prev.get("calls", []) + used_for))[:50]
        icon, label = SOURCE_TYPES.get(s["type"], ("•", s["type"]))
        attrs = {"source_type": s["type"], "type_label": label, "identity": f"{s['type']}:{s['name'].lower()}",
                 "addresses": addresses}
        for k in ("engine", "detail", "expression"):
            if s.get(k):
                attrs[k] = s[k]
        if used_for:
            attrs["calls"] = used_for
        desc = f"{label}{' (' + s['engine'] + ')' if s.get('engine') and s['engine'] != label else ''}: {s['name']}"
        cat.upsert_asset(key, "data_source", s["name"], source, parent=parent, description=desc, attrs=attrs)
        where = f"{evidence}:{s['line']}" if evidence and s.get("line") else evidence
        how = s["how"] + (f", in {s['function']}()" if s.get("function") else "")
        cat.add_link(src_key, s["relation"], key, source, dst_key=key, evidence=where, how=how)
        n += 1
    return n
