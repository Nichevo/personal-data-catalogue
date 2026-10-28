"""Power BI reports: .pbix, .pbit templates and .pbip project folders.

What is read, without Power BI installed:

  * pages and visuals, and the measures and columns each visual shows
    (Report/Layout, or the newer Report/definition files);
  * the queries (Power Query M), so a report links to the SQL Server tables,
    files and folders it loads;
  * the model: tables, columns, measures with their DAX, and relationships.
    .pbit and .pbip files keep these as plain text. A .pbix keeps them
    compressed in a format only Power BI writes; if the optional `pbixray`
    package is installed (pip install pbixray) it is used to read them,
    otherwise the report still shows its pages, visuals and the measures
    they use, and the page says how to get the rest (save a copy as .pbit or
    .pbip, or install pbixray);
  * live connections to a published dataset.

Measures become items of their own, so you can describe them, search for
them, and turn them into entries in the metric glossary.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from . import mquery
from .scanprogress import Reads

READER_VERSION = 3  # 2: passwords removed from stored Power Query text; 3: visual positions for wireframes
REPORT_SUFFIXES = (".pbix", ".pbit")


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    if len(data) > 1 and data[1:2] == b"\x00":
        return data.decode("utf-16-le")
    return data.decode("utf-8-sig", errors="replace")


def _json(data: bytes | str) -> Any:
    return json.loads(_decode(data) if isinstance(data, bytes) else data)


# ---- visuals ------------------------------------------------------------------------

def _fields_in(obj: Any, aliases: dict[str, str]) -> list[dict[str, str]]:
    """Every measure and column a visual's query mentions."""
    out: list[dict[str, str]] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for kind in ("Measure", "Column"):
                v = o.get(kind)
                if isinstance(v, dict) and "Property" in v:
                    ref = (v.get("Expression") or {}).get("SourceRef") or {}
                    table = ref.get("Entity") or aliases.get(ref.get("Source", ""), ref.get("Source", ""))
                    f = {"table": table, "name": v["Property"], "kind": kind.lower()}
                    if f not in out:
                        out.append(f)
            if "From" in o and isinstance(o["From"], list):
                for f in o["From"]:
                    if isinstance(f, dict) and f.get("Name") and f.get("Entity"):
                        aliases[f["Name"]] = f["Entity"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return out


def _title(cfg: dict) -> str:
    try:
        objs = (cfg.get("singleVisual") or cfg.get("visual") or {}).get("vcObjects") or \
            (cfg.get("visual") or {}).get("visualContainerObjects") or {}
        lit = objs["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"]
        return lit.strip("'")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _visual(cfg: dict) -> dict[str, Any] | None:
    sv = cfg.get("singleVisual") or cfg.get("visual") or {}
    vtype = sv.get("visualType")
    if not vtype:
        return None
    fields = _fields_in(sv, {})
    out = {"type": vtype, "title": _title(cfg), "fields": fields}
    labels = _display_names(sv)
    if labels:
        out["labels"] = labels
    return out


def _display_names(sv: dict) -> dict[str, str]:
    """Field ("Sales.Units") -> the name the visual shows for it, where someone renamed it in the visual."""
    out: dict[str, str] = {}
    for ref, props in (sv.get("columnProperties") or {}).items():
        if isinstance(props, dict) and props.get("displayName"):
            out[ref] = props["displayName"]
    state = ((sv.get("query") or {}).get("queryState") or {})
    for role in state.values():
        for p in (role or {}).get("projections", []) if isinstance(role, dict) else []:
            if isinstance(p, dict) and p.get("displayName") and p.get("queryRef"):
                out[p["queryRef"]] = p["displayName"]
    return out


def _pos(d: dict) -> list[float]:
    """[x, y, width, height] of a visual on its page."""
    try:
        return [round(float(d.get(k) or 0), 1) for k in ("x", "y", "width", "height")]
    except (TypeError, ValueError):
        return [0, 0, 0, 0]


def pages_from_layout(layout: dict) -> list[dict[str, Any]]:
    """Pages and visuals from the classic Report/Layout (or report.json) file."""
    pages = []
    for sec in layout.get("sections", []):
        visuals = []
        for vc in sec.get("visualContainers", []):
            try:
                cfg = json.loads(vc.get("config") or "{}")
            except ValueError:
                continue
            v = _visual(cfg)
            if v:
                v["pos"] = _pos(vc)
                visuals.append(v)
        pages.append({"name": sec.get("displayName") or sec.get("name", ""), "visuals": visuals,
                      "size": [sec.get("width") or 1280, sec.get("height") or 720]})
    return pages


def pages_from_definition(files: dict[str, bytes]) -> list[dict[str, Any]]:
    """Pages and visuals from the newer report format (definition/pages/<page>/visuals/<visual>/visual.json)."""
    pages: dict[str, dict[str, Any]] = {}
    for name, data in sorted(files.items()):
        m = re.search(r"definition/pages/([^/]+)/(page\.json|visuals/[^/]+/visual\.json)$", name)
        if not m:
            continue
        page = pages.setdefault(m.group(1), {"name": m.group(1), "visuals": []})
        try:
            doc = _json(data)
        except ValueError:
            continue
        if m.group(2) == "page.json":
            page["name"] = doc.get("displayName") or page["name"]
            page["size"] = [doc.get("width") or 1280, doc.get("height") or 720]
        else:
            v = _visual(doc)
            if v:
                v["pos"] = _pos(doc.get("position") or {})
                page["visuals"].append(v)
    return list(pages.values())


# ---- the model ----------------------------------------------------------------------------

def _expr(e: Any) -> str:
    return "\n".join(e) if isinstance(e, list) else (e or "")


def model_from_tmsl(doc: dict) -> dict[str, Any]:
    """Tables, measures, relationships and queries from DataModelSchema (.pbit) or model.bim (.pbip)."""
    model = doc.get("model", doc)
    shared = {e["name"]: _expr(e.get("expression")) for e in model.get("expressions", []) if e.get("name")}
    tables, measures, queries = [], [], []
    for t in model.get("tables", []):
        if t.get("isHidden") and t["name"].startswith(("LocalDateTable_", "DateTableTemplate_")):
            continue
        cols = [{"name": c["name"], "data_type": c.get("dataType", ""), "calculated": c.get("type") == "calculated",
                 "expression": _expr(c.get("expression"))} for c in t.get("columns", [])
                if not c.get("name", "").startswith("RowNumber-")]
        for m in t.get("measures", []):
            measures.append({"table": t["name"], "name": m["name"], "expression": _expr(m.get("expression")),
                             "description": m.get("description", ""), "format": m.get("formatString", ""),
                             "folder": m.get("displayFolder", "")})
        source = ""
        for p in t.get("partitions", []):
            src = p.get("source") or {}
            if src.get("type") in ("m", None) and src.get("expression"):
                source = _expr(src["expression"])
            elif src.get("type") == "calculated":
                source = ""
        tables.append({"name": t["name"], "columns": cols, "calculated": any(
            (p.get("source") or {}).get("type") == "calculated" for p in t.get("partitions", []))})
        if source:
            queries.append({"name": t["name"], "expression": source})
    for name, body in shared.items():
        queries.append({"name": name, "expression": body})
    rels = [{"from": f"{r.get('fromTable')}.{r.get('fromColumn')}", "to": f"{r.get('toTable')}.{r.get('toColumn')}",
             "active": r.get("isActive", True)} for r in model.get("relationships", [])]
    return {"tables": tables, "measures": measures, "relationships": rels, "queries": _analyse_queries(queries)}


def _analyse_queries(queries: list[dict[str, str]]) -> list[dict[str, Any]]:
    queries = [{**q, "expression": mquery.redact(q["expression"])} for q in queries]
    bodies = {q["name"]: q["expression"] for q in queries}
    params = mquery.parameters(bodies)
    out = []
    for q in queries:
        item = {"name": q["name"], "expression": q["expression"]}
        if q["name"] in params:
            item["parameter"] = params[q["name"]]
        else:
            item.update(mquery.analyse(q["expression"], params, [n for n in bodies if n != q["name"]]))
        out.append(item)
    return out


def _depth(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def model_from_tmdl(files: dict[str, str]) -> dict[str, Any]:
    """The same from a .pbip's TMDL files (definition/tables/*.tmdl, expressions.tmdl, relationships.tmdl)."""
    tables, measures, queries, rels = [], [], [], []

    def block(lines: list[str], i: int, min_depth: int) -> tuple[str, int]:
        out = []
        while i < len(lines) and (not lines[i].strip() or _depth(lines[i]) >= min_depth):
            if lines[i].strip() != "```":
                out.append(lines[i][min_depth:] if _depth(lines[i]) >= min_depth else "")
            i += 1
        return "\n".join(out).strip(), i

    def name_of(text: str) -> str:
        text = text.strip()
        return text[1:-1].replace("''", "'") if text.startswith("'") and text.endswith("'") else text

    for fname, text in sorted(files.items()):
        lines = text.replace("\r\n", "\n").split("\n")
        i, table = 0, None
        while i < len(lines):
            line = lines[i]
            s = line.strip()
            d = _depth(line)
            i += 1
            if d == 0 and s.startswith("table "):
                table = {"name": name_of(s[6:]), "columns": [], "calculated": False}
                tables.append(table)
            elif d == 0 and s.startswith("expression "):
                head, _, rest = s[11:].partition("=")
                body = rest.strip()
                if not body or body == "```":
                    body, i = block(lines, i, 2)
                queries.append({"name": name_of(head), "expression": re.sub(r"\s+meta\s*\[.*$", "", body, flags=re.S)
                                if body.startswith('"') else body})
            elif d == 0 and s.startswith("relationship "):
                rel: dict[str, Any] = {"active": True}
                while i < len(lines) and (_depth(lines[i]) >= 1 or not lines[i].strip()):
                    prop = lines[i].strip()
                    if prop.startswith("fromColumn:"):
                        rel["from"] = prop.split(":", 1)[1].strip().replace("'", "")
                    elif prop.startswith("toColumn:"):
                        rel["to"] = prop.split(":", 1)[1].strip().replace("'", "")
                    elif prop.startswith("isActive:"):
                        rel["active"] = prop.split(":", 1)[1].strip() != "false"
                    i += 1
                if "from" in rel and "to" in rel:
                    rels.append(rel)
            elif table is not None and d == 1 and s.startswith("measure "):
                head, _, rest = s[8:].partition("=")
                expr = rest.strip()
                if not expr or expr == "```":
                    expr, i = block(lines, i, 3)
                m = {"table": table["name"], "name": name_of(head), "expression": expr, "description": "",
                     "format": "", "folder": ""}
                while i < len(lines) and (_depth(lines[i]) >= 2 or not lines[i].strip()):
                    prop = lines[i].strip()
                    if prop.startswith("formatString:"):
                        m["format"] = prop.split(":", 1)[1].strip()
                    elif prop.startswith("displayFolder:"):
                        m["folder"] = prop.split(":", 1)[1].strip()
                    i += 1
                measures.append(m)
            elif table is not None and d == 1 and s.startswith("column "):
                head, _, rest = s[7:].partition("=")
                col = {"name": name_of(head), "data_type": "", "calculated": bool(rest.strip()),
                       "expression": rest.strip()}
                while i < len(lines) and (_depth(lines[i]) >= 2 or not lines[i].strip()):
                    prop = lines[i].strip()
                    if prop.startswith("dataType:"):
                        col["data_type"] = prop.split(":", 1)[1].strip()
                    i += 1
                if not col["name"].startswith("RowNumber-"):
                    table["columns"].append(col)
            elif table is not None and d == 1 and s.startswith("partition "):
                calc = s.rstrip().endswith("= calculated")
                table["calculated"] = table["calculated"] or calc
                while i < len(lines) and (_depth(lines[i]) >= 2 or not lines[i].strip()):
                    prop = lines[i].strip()
                    i += 1
                    if prop.startswith("source") and prop.rstrip().endswith("=") and not calc:
                        body, i = block(lines, i, 3)
                        queries.append({"name": table["name"], "expression": body})
    # /// descriptions above measures
    for fname, text in files.items():
        for m in re.finditer(r"((?:^\t*///.*\n)+)\t*measure\s+('(?:[^']|'')*'|[^=\n]+?)\s*=", text, re.M):
            desc = " ".join(l.strip()[3:].strip() for l in m.group(1).splitlines())
            name = m.group(2).strip()
            name = name[1:-1].replace("''", "'") if name.startswith("'") else name
            for meas in measures:
                if meas["name"] == name and not meas["description"]:
                    meas["description"] = desc
    return {"tables": tables, "measures": measures, "relationships": rels, "queries": _analyse_queries(queries)}


def _model_from_pbixray(path: Path) -> dict[str, Any] | None:
    try:
        from pbixray import PBIXRay  # optional
    except ImportError:
        return None
    try:
        x = PBIXRay(str(path))
        tables: dict[str, dict[str, Any]] = {}
        for _, r in x.schema.iterrows():
            tables.setdefault(r["TableName"], {"name": r["TableName"], "columns": [], "calculated": False})[
                "columns"].append({"name": r["ColumnName"], "data_type": str(r.get("PandasDataType", "")),
                                   "calculated": False, "expression": ""})
        measures = [{"table": r["TableName"], "name": r["Name"], "expression": r.get("Expression") or "",
                     "description": r.get("Description") or "", "format": "", "folder": r.get("DisplayFolder") or ""}
                    for _, r in x.dax_measures.iterrows()]
        queries = [{"name": r["TableName"], "expression": r["Expression"]} for _, r in x.power_query.iterrows()]
        rels = [{"from": f"{r['FromTableName']}.{r['FromColumnName']}", "to": f"{r['ToTableName']}.{r['ToColumnName']}",
                 "active": bool(r.get("IsActive", True))} for _, r in x.relationships.iterrows()]
        return {"tables": [t for n, t in tables.items() if not n.startswith(("LocalDateTable_", "DateTableTemplate_"))],
                "measures": measures, "relationships": rels, "queries": _analyse_queries(queries)}
    except Exception:  # pbixray is young; a file it can't read must not stop the scan
        return None


PBIXRAY_TIMEOUT = 180  # seconds; pbixray unpacks the whole model, which can take long on a big .pbix


def _model_in_own_process(path: Path, timeout: int = PBIXRAY_TIMEOUT) -> tuple[dict[str, Any] | None, str | None]:
    """The model read by pbixray in a separate process, so a huge or unusual .pbix can't hold up the scan.
    Returns (model, None), or (None, why not) when it ran out of time, or (None, None) without pbixray."""
    import importlib.util
    import os
    import subprocess
    import sys
    if importlib.util.find_spec("pbixray") is None:
        return None, None
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(Path(__file__).resolve().parents[1]),
                                                                     os.environ.get("PYTHONPATH")]))}
    try:
        done = subprocess.run([sys.executable, "-m", "pdcat.scan_powerbi", str(path)], capture_output=True,
                              timeout=timeout, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None, (f"The model wasn't read: pbixray was still unpacking it after {timeout} seconds, so it was "
                      "stopped to let the scan carry on. Save a copy as .pbit or .pbip next to it to read the model.")
    except OSError:
        return _model_from_pbixray(path), None
    lines = done.stdout.decode("utf-8", errors="replace").strip().splitlines()
    if done.returncode or not lines or not lines[-1].startswith(_MODEL_MARK):
        return None, None
    return json.loads(lines[-1][len(_MODEL_MARK):]), None


_MODEL_MARK = "pdcat-model:"


# ---- whole reports ------------------------------------------------------------------------------

def read_pbix(path: Path) -> dict[str, Any]:
    """A .pbix or .pbit file."""
    out: dict[str, Any] = {"format": path.suffix.lower().lstrip("."), "pages": [], "tables": [], "measures": [],
                           "relationships": [], "queries": [], "read_with": [], "not_read": []}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "Report/Layout" in names:
            out["pages"] = pages_from_layout(_json(z.read("Report/Layout")))
            out["read_with"].append("report layout")
        else:
            defs = {n: z.read(n) for n in names if n.startswith("Report/definition/") and n.endswith(".json")}
            if defs:
                out["pages"] = pages_from_definition(defs)
                out["read_with"].append("report definition")
        if "Connections" in names:
            try:
                conns = _json(z.read("Connections")).get("Connections", [])
                live = [c for c in conns if "live" in (c.get("ConnectionType") or "").lower()
                        or "pbiazure" in (c.get("ConnectionString") or "")]
                if live:
                    c = live[0]
                    db = re.search(r"Initial Catalog=([^;]+)", c.get("ConnectionString") or "")
                    out["live"] = {"dataset": c.get("PbiModelDatabaseName") or (db.group(1) if db else ""),
                                   "type": c.get("ConnectionType", "")}
            except ValueError:
                pass
        model = None
        if "DataModelSchema" in names:
            model = model_from_tmsl(_json(z.read("DataModelSchema")))
            out["read_with"].append("model schema")
        if "DataMashup" in names:
            try:
                parts = mquery.package_from_mashup(z.read("DataMashup"))
                section = parts.get("Formulas/Section1.m")
                if section and not (model and model["queries"]):
                    out["queries"] = mquery.read_queries(section)
                    out["read_with"].append("Power Query")
            except (ValueError, zipfile.BadZipFile):
                pass
        has_model = "DataModel" in names
    if model is None and has_model:
        model, problem = _model_in_own_process(path)
        if model:
            out["read_with"].append("pbixray")
        elif problem:
            out["not_read"].append(problem)
        else:
            out["not_read"].append("The model (tables, DAX measures, queries) is compressed inside the .pbix. "
                                   "Install pbixray (pip install pbixray), or save a copy as .pbit or .pbip next to "
                                   "it, to read it.")
    if model:
        queries = model.pop("queries")
        out.update(model)
        if queries:
            out["queries"] = queries
    _measures_from_visuals(out)
    return out


def read_pbip(folder: Path) -> dict[str, Any]:
    """A .pbip project: the X.SemanticModel and X.Report folders next to X.pbip."""
    stem = folder.stem
    base = folder.parent
    out: dict[str, Any] = {"format": "pbip", "pages": [], "tables": [], "measures": [], "relationships": [],
                           "queries": [], "read_with": [], "not_read": []}
    report_dir = base / f"{stem}.Report"
    if (report_dir / "report.json").is_file():
        out["pages"] = pages_from_layout(_json((report_dir / "report.json").read_bytes()))
        out["read_with"].append("report layout")
    elif (report_dir / "definition").is_dir():
        files = {p.relative_to(report_dir).as_posix(): p.read_bytes() for p in (report_dir / "definition").rglob("*.json")}
        out["pages"] = pages_from_definition(files)
        out["read_with"].append("report definition")
    model_dir = base / f"{stem}.SemanticModel"
    if not model_dir.is_dir():
        pbir = report_dir / "definition.pbir"
        if pbir.is_file():
            try:
                ref = _json(pbir.read_bytes()).get("datasetReference", {}).get("byPath", {}).get("path")
                if ref:
                    model_dir = (report_dir / ref).resolve()
            except ValueError:
                pass
    if (model_dir / "model.bim").is_file():
        model = model_from_tmsl(_json((model_dir / "model.bim").read_bytes()))
        out["read_with"].append("model.bim")
    elif (model_dir / "definition").is_dir():
        files = {p.relative_to(model_dir).as_posix(): p.read_text(encoding="utf-8-sig", errors="replace")
                 for p in (model_dir / "definition").rglob("*.tmdl")}
        model = model_from_tmdl(files)
        out["read_with"].append("TMDL")
    else:
        model = None
    if model:
        out.update(model)
    _measures_from_visuals(out)
    return out


def _measures_from_visuals(out: dict[str, Any]) -> None:
    """Which pages use each measure; and measures known only from visuals when the model couldn't be read."""
    used: dict[tuple[str, str], list[str]] = {}
    for p in out["pages"]:
        for v in p["visuals"]:
            for f in v["fields"]:
                if f["kind"] == "measure":
                    pages = used.setdefault((f["table"], f["name"]), [])
                    if p["name"] not in pages:
                        pages.append(p["name"])
    known = {(m["table"], m["name"]) for m in out["measures"]}
    for (table, name) in used:
        if (table, name) not in known:
            out["measures"].append({"table": table, "name": name, "expression": "", "description": "", "format": "",
                                    "folder": "", "from_visuals_only": True})
    for m in out["measures"]:
        m["used_on"] = used.get((m["table"], m["name"]), [])


def read_report(path: Path) -> dict[str, Any]:
    return read_pbip(path) if path.suffix.lower() == ".pbip" else read_pbix(path)


def measure_key(report_key: str, table: str, name: str) -> str:
    return f"{report_key}/measures/{table}.{name}".lower()


def ingest_report(cat, path: Path, key: str, name: str, source: str, parent: str,
                  reads: Reads | None = None) -> str | None:
    """Catalogue one report and its measures, with links to what its queries load. Returns an error, if any."""
    from datetime import datetime, timezone

    from .duplicates import fingerprint
    st = path.stat()
    previous = cat.get_asset(key)
    if previous and previous["attrs"].get("size_bytes") == st.st_size and previous["attrs"].get("mtime") == \
            st.st_mtime and previous["attrs"].get("reader") == READER_VERSION:
        info = previous["attrs"]
    else:
        try:
            info = (reads or Reads()).get(read_report, path)
        except Exception as e:  # a damaged file must not stop the scan
            info = {"format": path.suffix.lower().lstrip("."), "read_error": f"{type(e).__name__}: {e}", "pages": [],
                    "tables": [], "measures": [], "relationships": [], "queries": [], "read_with": [], "not_read": []}
        info.update({"path": str(path), "size_bytes": st.st_size, "mtime": st.st_mtime, "reader": READER_VERSION,
                     "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                     "checksum": (reads or Reads()).get(fingerprint, path) if path.is_file() else None})
    visuals = sum(len(p["visuals"]) for p in info["pages"])
    desc = f"Power BI report: {len(info['pages'])} pages, {visuals} visuals, {len(info['measures'])} measures"
    if info.get("live"):
        desc += f", live connection to {info['live']['dataset'] or 'a published dataset'}"
    cat.upsert_asset(key, "report", name, source, parent=parent, description=desc, attrs=info)
    cat.set_columns(key, [{"name": f"{t['name']}.{c['name']}", "ordinal": i, "data_type": c["data_type"]}
                          for t in info["tables"] for i, c in enumerate(t["columns"])])
    for m in info["measures"]:
        cat.upsert_asset(measure_key(key, m["table"], m["name"]), "measure", f"{m['table']}[{m['name']}]", source,
                         parent=key, description=m.get("description") or None, attrs=m)
    link_queries(cat, key, source, info["queries"])
    return info.get("read_error")


def link_queries(cat, key: str, source: str, queries: list[dict[str, Any]]) -> None:
    """Links from a report or workbook to the tables, files and folders its queries load."""
    for q in queries:
        how = f"Power Query “{q['name']}”"
        hint = q["databases"][0] if q.get("databases") else None
        for ref in mquery.table_refs(q):
            cat.add_link(key, "reads", ref, source, hint=hint, how=how)
        for f in q.get("files", []):
            cat.add_link(key, "reads", f"file:{f}", source, how=how)


if __name__ == "__main__":  # python -m pdcat.scan_powerbi report.pbix: the model as JSON, for _model_in_own_process
    import sys
    print(_MODEL_MARK + json.dumps(_model_from_pbixray(Path(sys.argv[1])), default=str))
