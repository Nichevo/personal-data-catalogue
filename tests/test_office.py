"""Power BI reports and Excel workbooks: queries, visuals, measures, connections, pivots and macros."""
import base64
import io
import json
import struct
import zipfile

from pdcat import mquery
from pdcat.scan_excel import decompress_vba, read_workbook, sql_in_vba
from pdcat.scan_files import scan_folder
from pdcat.scan_powerbi import read_pbip, read_pbix

SALES = "sql://warehouse/forecastdb/dbo.salesdaily"
STORE = "sql://warehouse/forecastdb/dbo.store"

SECTION = '''section Section1;

shared ServerName = "DEMO-SQL01" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true];

shared Sales = let
    Source = Sql.Database(ServerName, "ForecastDB"),
    dbo_SalesDaily = Source{[Schema="dbo",Item="SalesDaily"]}[Data]
in
    dbo_SalesDaily;

shared #"Store list" = let
    Source = Sql.Database("DEMO-SQL01", "ForecastDB", [Query="SELECT StoreId, Region#(lf)FROM dbo.Store WHERE Region <> ""X"""]),
    Joined = Table.NestedJoin(Source, {"StoreId"}, Sales, {"StoreId"}, "s")
in
    Joined;

shared Targets = Csv.Document(File.Contents("S:\\Sales\\targets.csv"), [Delimiter=","]);
'''


def mashup(section: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Config/Package.xml", "<Package/>")
        z.writestr("Formulas/Section1.m", section)
    pkg = buf.getvalue()
    return struct.pack("<II", 0, len(pkg)) + pkg + struct.pack("<I", 0)


def layout() -> dict:
    visual = {"name": "v1", "singleVisual": {
        "visualType": "clusteredBarChart",
        "prototypeQuery": {"From": [{"Name": "s", "Entity": "Sales"}, {"Name": "t", "Entity": "Store"}],
                           "Select": [{"Measure": {"Expression": {"SourceRef": {"Source": "s"}},
                                                   "Property": "Total Units"}, "Name": "Sales.Total Units"},
                                      {"Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": "Region"},
                                       "Name": "Store.Region"}]},
        "vcObjects": {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": "'Units by region'"}}}}}]}}}
    return {"sections": [{"displayName": "Overview", "visualContainers": [{"config": json.dumps(visual)}]}]}


TMSL = {"model": {
    "tables": [{"name": "Sales", "columns": [{"name": "Units", "dataType": "decimal"},
                                             {"name": "Big", "dataType": "boolean", "type": "calculated",
                                              "expression": "Sales[Units] > 100"}],
                "measures": [{"name": "Total Units", "expression": "SUM(Sales[Units])", "formatString": "0",
                              "description": "Units sold, net of returns"}],
                "partitions": [{"name": "Sales", "source": {"type": "m", "expression": [
                    "let", "    Source = Sql.Database(ServerName, \"ForecastDB\"),",
                    "    t = Source{[Schema=\"dbo\",Item=\"SalesDaily\"]}[Data]", "in", "    t"]}}]},
               {"name": "Store", "columns": [{"name": "Region", "dataType": "string"}], "partitions": []}],
    "relationships": [{"fromTable": "Sales", "fromColumn": "StoreId", "toTable": "Store", "toColumn": "StoreId"}],
    "expressions": [{"name": "ServerName", "kind": "m",
                     "expression": "\"DEMO-SQL01\" meta [IsParameterQuery=true, Type=\"Text\"]"}]}}


def write_pbit(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(layout()).encode("utf-16-le"))
        z.writestr("DataModelSchema", json.dumps(TMSL).encode("utf-16-le"))


def write_pbix(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Report/Layout", json.dumps(layout()).encode("utf-16-le"))
        z.writestr("DataMashup", mashup(SECTION))
        z.writestr("DataModel", b"\x00compressed")


def test_m_queries():
    qs = {q["name"]: q for q in mquery.read_queries(SECTION)}
    assert qs["ServerName"]["parameter"] == "DEMO-SQL01"
    assert qs["Sales"]["servers"] == ["DEMO-SQL01"] and qs["Sales"]["tables"] == ["ForecastDB.dbo.SalesDaily"]
    store = qs["Store list"]
    assert store["tables"] == ["ForecastDB.dbo.store"] and 'Region <> "X"' in store["sql"][0]
    assert store["uses"] == ["Sales"]
    assert qs["Targets"]["files"] == ["S:\\Sales\\targets.csv"] and "uses" not in qs["Targets"]
    assert mquery.redact("Server=x;Password=s3cret;Database=y") == "Server=x;Password=***;Database=y"


def test_pbit_model_and_visuals(tmp_path):
    p = tmp_path / "Sales.pbit"
    write_pbit(p)
    r = read_pbix(p)
    assert r["pages"][0]["visuals"][0]["title"] == "Units by region"
    assert {f["name"] for f in r["pages"][0]["visuals"][0]["fields"]} == {"Total Units", "Region"}
    m = r["measures"][0]
    assert (m["table"], m["name"], m["expression"], m["used_on"]) == ("Sales", "Total Units", "SUM(Sales[Units])",
                                                                      ["Overview"])
    sales_q = next(q for q in r["queries"] if q["name"] == "Sales")
    assert sales_q["servers"] == ["DEMO-SQL01"] and sales_q["tables"] == ["ForecastDB.dbo.SalesDaily"]
    assert r["relationships"][0]["from"] == "Sales.StoreId"


def test_pbix_without_pbixray_still_reads_layout_and_queries(tmp_path):
    p = tmp_path / "Sales.pbix"
    write_pbix(p)
    r = read_pbix(p)
    assert r["measures"][0]["from_visuals_only"] and r["measures"][0]["name"] == "Total Units"
    assert {q["name"] for q in r["queries"]} == {"ServerName", "Sales", "Store list", "Targets"}
    assert r["not_read"] and "pbixray" in r["not_read"][0]


def test_pbip_tmdl(tmp_path):
    (tmp_path / "Sales.pbip").write_text("{}")
    tables = tmp_path / "Sales.SemanticModel" / "definition" / "tables"
    tables.mkdir(parents=True)
    (tables / "Sales.tmdl").write_text(
        "table Sales\n"
        "\t/// Units sold, net of returns\n"
        "\tmeasure 'Total Units' = SUM(Sales[Units])\n"
        "\t\tformatString: 0\n"
        "\tmeasure Margin =\n"
        "\t\t\tDIVIDE(\n"
        "\t\t\t\t[Profit], [Revenue])\n"
        "\t\tdisplayFolder: Money\n"
        "\tcolumn Units\n"
        "\t\tdataType: decimal\n"
        "\tpartition Sales = m\n"
        "\t\tmode: import\n"
        "\t\tsource =\n"
        "\t\t\t\tlet\n"
        "\t\t\t\t    Source = Sql.Database(\"DEMO-SQL01\", \"ForecastDB\"),\n"
        "\t\t\t\t    t = Source{[Schema=\"dbo\",Item=\"SalesDaily\"]}[Data]\n"
        "\t\t\t\tin\n"
        "\t\t\t\t    t\n")
    r = read_pbip(tmp_path / "Sales.pbip")
    ms = {m["name"]: m for m in r["measures"]}
    assert ms["Total Units"]["description"] == "Units sold, net of returns" and ms["Total Units"]["format"] == "0"
    assert ms["Margin"]["expression"].replace("\n", " ").split() == ["DIVIDE(", "[Profit],", "[Revenue])"]
    assert ms["Margin"]["folder"] == "Money"
    assert r["tables"][0]["columns"][0] == {"name": "Units", "data_type": "decimal", "calculated": False,
                                            "expression": ""}
    assert r["queries"][0]["tables"] == ["ForecastDB.dbo.SalesDaily"]


# ---- Excel ----------------------------------------------------------------------------------

def vba_compress(text: bytes) -> bytes:
    """Literal-only MS-OVBA compression, enough to test the reader."""
    out = bytearray(b"\x01")
    for start in range(0, len(text), 4096):
        chunk = text[start:start + 4096]
        if len(chunk) == 4096:  # too big to store as literals: stored as is
            out += struct.pack("<H", 0x3000 | 0xFFF) + chunk
            continue
        body = bytearray()
        for i in range(0, len(chunk), 8):
            body.append(0)
            body += chunk[i:i + 8]
        out += struct.pack("<H", 0x8000 | 0x3000 | (len(body) + 2 - 3)) + body
    return bytes(out)


VBA = b'''Attribute VB_Name = "LoadData"
Sub Refresh()
    Dim sql As String
    sql = "SELECT s.StoreId, SUM(s.Units) " & _
          "FROM dbo.SalesDaily s "
    sql = sql & "GROUP BY s.StoreId"
    conn.Execute "INSERT INTO ml.ForecastOutput (StoreId) VALUES (1)"
    MsgBox "Done"
End Sub
'''


def write_xlsx(path):
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    m = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    r = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {m} {r}><sheets><sheet name="Sales" sheetId="1" r:id="rId1"/>'
                                      '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", f'<sst {m}><si><t>StoreId</t></si><si><t>Units</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {m}><dimension ref="A1:B3"/><sheetData>'
                   '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                   '<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>5</v></c></row></sheetData></worksheet>')
        z.writestr("xl/worksheets/_rels/sheet1.xml.rels", rels.replace("worksheets/sheet1.xml", "../tables/table1.xml"))
        z.writestr("xl/tables/table1.xml", f'<table {m} id="1" name="Sales" tableType="queryTable"/>')
        z.writestr("xl/tables/_rels/table1.xml.rels",
                   rels.replace("worksheets/sheet1.xml", "../queryTables/queryTable1.xml"))
        z.writestr("xl/queryTables/queryTable1.xml", f'<queryTable {m} name="ExternalData_1" connectionId="1"/>')
        z.writestr("xl/connections.xml", f'<connections {m}>'
                   '<connection id="1" name="Query - Sales" type="5"><dbPr connection="Provider=Microsoft.Mashup.OleDb.1;'
                   'Data Source=$Workbook$;Location=Sales" command="SELECT * FROM [Sales]"/></connection>'
                   '<connection id="2" name="Warehouse" type="1"><dbPr connection="DSN=Warehouse;UID=sam;PWD=hunter2;'
                   'DATABASE=ForecastDB" command="SELECT Region FROM dbo.Store"/></connection></connections>')
        z.writestr("xl/pivotCache/pivotCacheDefinition1.xml", f'<pivotCacheDefinition {m}>'
                   '<cacheSource type="external" connectionId="2"/><cacheFields count="1">'
                   '<cacheField name="Region"/></cacheFields></pivotCacheDefinition>')
        b64 = base64.b64encode(mashup(SECTION)).decode()
        z.writestr("customXml/item1.xml", ('<?xml version="1.0" encoding="utf-16"?><DataMashup '
                                           'xmlns="http://schemas.microsoft.com/DataMashup">' + b64 +
                                           '</DataMashup>').encode("utf-16"))
        z.writestr("xl/vbaProject.bin", b"\xd0\xcf\x11\xe0junk" + b"\x00" * 50 + vba_compress(VBA) + b"\x00" * 20)


def test_vba_reading():
    assert decompress_vba(vba_compress(VBA * 40)) == VBA * 40  # several chunks
    sqls = sql_in_vba(VBA.decode())
    assert "SELECT s.StoreId, SUM(s.Units) FROM dbo.SalesDaily s GROUP BY s.StoreId" in sqls
    assert any(s.startswith("INSERT INTO ml.ForecastOutput") for s in sqls) and "Done" not in sqls


def test_workbook(tmp_path):
    p = tmp_path / "Sales.xlsm"
    write_xlsx(p)
    w = read_workbook(p)
    sheet = w["sheets"][0]
    assert sheet["header"] == ["StoreId", "Units"] and sheet["range"] == "A1:B3"
    assert sheet["loaded_from"] == ["Query - Sales"]
    conns = {c["name"]: c for c in w["connections"]}
    assert conns["Query - Sales"]["type"] == "Power Query" and conns["Query - Sales"]["query"] == "Sales"
    assert conns["Warehouse"]["type"] == "ODBC" and "hunter2" not in conns["Warehouse"]["connection"]
    assert w["pivots"][0] == {"fields": ["Region"], "source": "connection “Warehouse”"}
    assert {q["name"] for q in w["queries"]} >= {"Sales", "Store list"}
    assert w["macros"] == ["LoadData"] and len(w["vba_sql"]) == 2


def test_folder_scan_links_reports_and_workbooks(catalogue, tmp_path):
    folder = tmp_path / "reports"
    folder.mkdir()
    write_pbit(folder / "Sales.pbit")
    write_xlsx(folder / "Sales.xlsm")
    counts = scan_folder(catalogue, "reports", folder)
    assert counts["reports"] == 1 and counts["workbooks"] == 1 and counts["errors"] == 0
    report, book = "files://reports/Sales.pbit", "files://reports/Sales.xlsm"
    assert catalogue.get_asset(report)["kind"] == "report"
    readers = {l["src_key"] for l in catalogue.links_to(SALES)}
    assert {report, book} <= readers
    assert book in {l["src_key"] for l in catalogue.links_to(STORE)}  # from the Microsoft Query connection
    measure = [c for c in catalogue.children(report) if c["kind"] == "measure"][0]
    assert measure["name"] == "Sales[Total Units]" and measure["description"] == "Units sold, net of returns"
    assert any(r["key"] == measure["key"] for r in catalogue.search("total units"))
    from pdcat import aipack
    assert "SUM(Sales[Units])" in aipack.build(catalogue)["reports.md"]  # the Copilot pack lists its measures
    # Rescanning an unchanged file reuses what was read.
    assert scan_folder(catalogue, "reports", folder)["reports"] == 1
    assert catalogue.get_asset(measure["key"])["status"] == "active"


def test_stored_query_text_has_no_passwords():
    section = ('section Section1;\nshared Sales = let Source = Odbc.Query("dsn=Sales;uid=me;pwd=s3cret", '
               '"SELECT * FROM dbo.Sales") in Source;\n'
               'shared Api = let Source = Web.Contents("https://me:t0ken@example.com/data") in Source;')
    queries = mquery.read_queries(section)
    text = json.dumps(queries)
    assert "s3cret" not in text and "t0ken" not in text
    assert "pwd=***" in text and "me:***@example.com" in text
