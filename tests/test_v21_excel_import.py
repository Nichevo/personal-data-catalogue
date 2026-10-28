"""Version 2.1: importing SQL queries, metrics and knowledge from an Excel sheet."""
import io
import zipfile

from pdcat import excelimport as X


def xlsx(sheets: dict[str, list[list[str]]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        wb = ['<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
              'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
        rels = ['<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
        for i, (name, rows) in enumerate(sheets.items(), start=1):
            wb.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>')
            rels.append(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml" Type="x"/>')
            xml_rows = []
            for r, row in enumerate(rows, start=1):
                cells = "".join(f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{v}</t></is></c>'
                                for c, v in enumerate(row) if v != "")
                xml_rows.append(f'<row r="{r}">{cells}</row>')
            z.writestr(f"xl/worksheets/sheet{i}.xml",
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                       + "".join(xml_rows) + "</sheetData></worksheet>")
        z.writestr("xl/workbook.xml", "".join(wb) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels) + "</Relationships>")
    return buf.getvalue()


def test_sql_rows_become_pasted_sql(catalogue):
    data = xlsx({"Queries": [["Query name", "SQL", "Notes"],
                             ["Daily sales", "SELECT * FROM dbo.SalesDaily", "used for the weekly pack"],
                             ["", "", ""],
                             ["No SQL", "", "skipped"]],
                 "Other": [["x"]]})
    assert X.sheet_names(data) == ["Queries", "Other"]
    cols, recs = X.as_records(X.read_sheet(data, "Queries"))
    mapping = X.guess_mapping("sql", cols)
    assert mapping["sql"] == "SQL" and mapping["title"] == "Query name" and mapping["notes"] == "Notes"
    items = X.preview("sql", recs, mapping)
    assert len(items) == 1
    assert X.import_rows(catalogue, "sql", items, "queries.xlsx", "Queries") == 1
    [snip] = [a for a in catalogue.list_assets() if a["kind"] == "sql_snippet"]
    a = catalogue.get_asset(snip["key"])
    assert a["name"] == "Daily sales" and a["attrs"]["imported_from_workbook"] == "queries.xlsx › Queries"
    assert "sql://warehouse/forecastdb/dbo.salesdaily" in {l["dst_key"] for l in catalogue.links_from(snip["key"])}


def test_metrics_with_blank_cells_and_csv(catalogue):
    from pdcat.knowledge import list_metrics
    data = xlsx({"KPIs": [["Metric", "Definition", "", "Formula"],
                          ["Forecast accuracy", "1 - WAPE", "", "1 - sum(|a-f|)/sum(a)"]]})
    rows = X.read_sheet(data, "KPIs")
    assert rows[1] == ["Forecast accuracy", "1 - WAPE", "", "1 - sum(|a-f|)/sum(a)"]
    cols, recs = X.as_records(rows)
    items = X.preview("metric", recs, X.guess_mapping("metric", cols))
    X.import_rows(catalogue, "metric", items, "kpis.xlsx", "KPIs")
    [m] = [m for m in list_metrics(catalogue) if m["name"] == "Forecast accuracy"]
    assert m["formula"] == "1 - sum(|a-f|)/sum(a)" and "kpis.xlsx" in m["caveats"]
    csv_rows = X.read_sheet(b"title;body\nFreight;Always sea freight\n", "", "notes.csv")
    assert X.as_records(csv_rows)[1] == [{"title": "Freight", "body": "Always sea freight"}]


def test_whole_sheet_as_one_note(catalogue):
    from pdcat.knowledge import get_knowledge
    uid = X.import_whole_sheet(catalogue, [["Freight is sea freight"], ["", "Unless marked AIR"]], "Freight rules",
                               "rules.xlsx", "Sheet1")
    body = get_knowledge(catalogue, uid)["body"]
    assert "Freight is sea freight\nUnless marked AIR" in body and "rules.xlsx" in body
