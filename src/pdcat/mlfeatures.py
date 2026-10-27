"""A feature dictionary for machine learning projects.

For each feature: what it means, where it comes from (tables and columns), the
function that builds it, the entity and grain, any lag or window, whether it
is known at prediction time (leakage), how missing values and categories are
handled, which models use it and how important it is to them.

Suggestions come from two places:

  * your code: columns assigned in feature-building functions
    (df["sales_lag_7"] = df["sales"].shift(7)), with lags, windows and
    encodings read from the expression. A negative shift reads a future value
    and is flagged as likely leakage.
  * your saved models: LightGBM text and JSON model files list their
    features and importances. scikit-learn models are pickles, which are never
    opened (loading a pickle can run code); save the importances next to the
    model as a CSV (columns feature, importance) and they're read from that.

The dictionary also points out features no model uses, features that look
like duplicates, and drift against a baseline you set.
"""
from __future__ import annotations

import ast
import json
import re
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from .store import Catalogue, now

MODEL_SUFFIXES = (".txt", ".model", ".json", ".lgb", ".lgbm")
PICKLE_SUFFIXES = (".pkl", ".pickle", ".joblib", ".sav")
FEATURE_FN = re.compile(r"feat|engineer|transform|prepare|build_x|make_x|lag|rolling", re.I)
ENCODERS = {"get_dummies": "one-hot", "OneHotEncoder": "one-hot", "LabelEncoder": "label codes",
            "OrdinalEncoder": "ordinal codes", "TargetEncoder": "target encoding", "factorize": "label codes",
            "category": "pandas category"}


# ---- reading code -----------------------------------------------------------------

def _src(node: ast.AST, text: str | None = None) -> str:
    try:
        return ast.unparse(node)[:300]
    except Exception:
        return ""


def describe_expression(expr: str) -> dict[str, str]:
    """Lag, window, missing handling, encoding and leakage from a pandas expression."""
    out: dict[str, str] = {}
    shifts = re.findall(r"\.shift\(\s*(?:periods\s*=\s*)?(-?\d+)", expr)
    if shifts:
        lags = [int(s) for s in shifts]
        if any(l < 0 for l in lags):
            out["known_at_prediction"] = "no"
            out["leakage_reason"] = f"uses a future value (shift({min(lags)}))"
        out["window"] = ", ".join(f"lag {l}" for l in lags)
    roll = re.findall(r"\.(rolling|expanding|ewm)\(\s*(?:window\s*=\s*|span\s*=\s*|halflife\s*=\s*)?([^,)]*)", expr)
    if roll:
        w = ", ".join(f"{k} {v.strip()}".strip() for k, v in roll)
        out["window"] = f"{out['window']}, {w}" if out.get("window") else w
        if "center=True" in expr.replace(" ", ""):
            out["known_at_prediction"] = "no"
            out["leakage_reason"] = "centred window uses future rows"
    if ".diff(" in expr or ".pct_change(" in expr:
        out["window"] = (out.get("window", "") + ", " if out.get("window") else "") + "change from previous"
    m = re.search(r"\.fillna\(([^)]*)\)", expr)
    if m:
        out["missing"] = f"filled with {m.group(1).strip()[:40]}"
    elif ".dropna(" in expr:
        out["missing"] = "rows dropped"
    for k, v in ENCODERS.items():
        if k in expr:
            out["encoding"] = v
    if "groupby(" in expr:
        g = re.search(r"groupby\(\s*([^)]*)\)", expr)
        if g:
            out["entity"] = g.group(1).strip("[]'\" ")[:60]
    return out


def feature_assignments(tree: ast.AST) -> list[dict[str, Any]]:
    """Columns assigned in feature-building code: df["x"] = …, df.assign(x=…), df.loc[:, "x"] = …"""
    found = []

    def visit(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = fn
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            # In feature-building functions every new column counts; elsewhere only ones with a lag, window or encoding.
            if isinstance(child, ast.Assign) and (FEATURE_FN.search(name or "")
                                                  or describe_expression(_src(child.value))):
                for t in child.targets:
                    col = _target_column(t)
                    if col:
                        found.append(_feature(col, name, child.lineno, child.value))
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "assign" \
                    and FEATURE_FN.search(name or ""):
                for kw in child.keywords:
                    if kw.arg:
                        found.append(_feature(kw.arg, name, child.lineno, kw.value))
            visit(child, name)

    visit(tree, "")
    seen, out = set(), []
    for f in found:
        if f["name"] not in seen:
            seen.add(f["name"])
            out.append(f)
    return out


def _target_column(t: ast.AST) -> str | None:
    if isinstance(t, ast.Subscript):
        sl = t.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            return sl.value
        if isinstance(sl, ast.Tuple) and sl.elts and isinstance(sl.elts[-1], ast.Constant) \
                and isinstance(sl.elts[-1].value, str):
            return sl.elts[-1].value
    return None


def _feature(name: str, fn: str, line: int, value: ast.AST) -> dict[str, Any]:
    expr = _src(value)
    used = sorted({n.slice.value for n in ast.walk(value) if isinstance(n, ast.Subscript)
                   and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)} - {name})
    return {"name": name, "function": fn or "(script)", "line": line, "expression": expr, "uses": used,
            **describe_expression(expr)}


# ---- reading model files (never unpickling) --------------------------------------------

def read_model_file(path: Path) -> dict[str, Any] | None:
    """Features and importances from a LightGBM text or JSON model, or an importance CSV.
    Pickles are listed but never opened."""
    suffix = path.suffix.lower()
    if suffix in PICKLE_SUFFIXES:
        return {"format": "pickle", "features": [], "importance": {},
                "note": "Saved with pickle/joblib, which isn't opened because loading it could run code. Save the "
                        "importances next to it as a CSV (feature, importance) to see them here."}
    if suffix == ".csv":
        return _importance_csv(path)
    if suffix not in MODEL_SUFFIXES or path.stat().st_size > 200 * 1024 * 1024:
        return None
    with path.open(encoding="utf-8", errors="replace") as f:
        head = f.read(4096)
    if suffix == ".json" or head.lstrip().startswith("{"):
        if '"feature_names"' not in head and "feature_names" not in path.read_text(errors="replace")[:200000]:
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            return None
        if "feature_names" not in data:
            return None
        imp = data.get("feature_importances") or {}
        return {"format": "LightGBM JSON", "features": list(data["feature_names"]), "importance": imp,
                "objective": data.get("objective"), "trees": len(data.get("tree_info", []))}
    if not (head.startswith("tree\n") or "\nversion=" in head[:200] or head.startswith("tree\r\n")):
        return None
    features, importance, objective, trees, in_imp = [], {}, None, 0, False
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("feature_names="):
                features = line.split("=", 1)[1].split()
            elif line.startswith("objective="):
                objective = line.split("=", 1)[1]
            elif line.startswith("Tree="):
                trees += 1
            elif line == "feature_importances:":
                in_imp = True
            elif in_imp:
                if not line or line.startswith(("parameters:", "pandas_categorical")):
                    in_imp = False
                elif "=" in line:
                    k, v = line.rsplit("=", 1)
                    try:
                        importance[k] = float(v)
                    except ValueError:
                        pass
    if not features:
        return None
    return {"format": "LightGBM text", "features": features, "importance": importance, "objective": objective,
            "trees": trees, "importance_type": "split count"}


def _importance_csv(path: Path) -> dict[str, Any] | None:
    import pandas as pd
    try:
        df = pd.read_csv(path, nrows=5000)
    except Exception:
        return None
    cols = {c.lower(): c for c in df.columns}
    fcol = cols.get("feature") or cols.get("feature_name") or cols.get("name")
    icol = cols.get("importance") or cols.get("gain") or cols.get("split") or cols.get("value")
    if not fcol or not icol or len(df.columns) > 4:
        return None
    imp = {str(r[fcol]): float(r[icol]) for _, r in df.iterrows() if pd.notna(r[icol])}
    return {"format": "importance CSV", "features": list(imp), "importance": imp}


def scan_models(cat: Catalogue, root: Path, project_key: str, source: str, skip: set[str]) -> int:
    """Catalogue model files in a project. Call inside the project's scan."""
    n = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(p in skip for p in path.relative_to(root).parts[:-1]):
            continue
        suffix = path.suffix.lower()
        if suffix not in MODEL_SUFFIXES + PICKLE_SUFFIXES and not (suffix == ".csv" and "import" in path.stem.lower()):
            continue
        if suffix in (".txt", ".json") and not re.search(r"model|lgb|lightgbm|booster|gbm", path.stem, re.I):
            continue
        try:
            info = read_model_file(path)
        except OSError:
            continue
        if not info:
            continue
        rel = path.relative_to(root).as_posix()
        st = path.stat()
        from datetime import datetime, timezone
        cat.upsert_asset(f"{project_key}/{rel}", "model", rel, source, parent=project_key,
                         description=f"{info['format']} model with {len(info['features'])} features"
                         if info["features"] else info.get("note"),
                         attrs={**info, "path": str(path), "size_bytes": st.st_size,
                                "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds")})
        n += 1
    return n


# ---- the dictionary -------------------------------------------------------------------

FIELDS = ("name", "project", "description", "entity", "grain", "sources", "built_by", "expression", "window",
          "known_at_prediction", "leakage_reason", "missing", "encoding", "data_type", "models", "importance",
          "status", "owner", "state", "baseline")
JSON_FIELDS = {"sources", "models", "importance"}


def save_feature(cat: Catalogue, name: str, uid: str | None = None, **fields: Any) -> str:
    bad = set(fields) - set(FIELDS)
    if bad:
        raise ValueError(f"unknown field(s): {', '.join(sorted(bad))}")
    if not name.strip():
        raise ValueError("A feature needs a name.")
    old = get_feature(cat, uid) if uid else None
    merged = {f: (old or {}).get(f) for f in FIELDS}
    merged.update(fields)
    merged["name"] = name.strip()
    merged["state"] = merged.get("state") or "confirmed"
    for f in JSON_FIELDS:
        merged[f] = json.dumps(merged.get(f) or ({} if f == "importance" else []))
    uid = uid or uuid.uuid4().hex
    ts = now()
    cols = [f for f in FIELDS if f != "name"]
    with cat.conn:
        cat.conn.execute(
            f"INSERT INTO features (uid, name, {', '.join(cols)}, author, created_at, updated_at) "
            f"VALUES (?, ?, {', '.join('?' * len(cols))}, ?, ?, ?) ON CONFLICT(uid) DO UPDATE SET name = excluded.name, "
            + ", ".join(f"{c} = excluded.{c}" for c in cols) + ", updated_at = excluded.updated_at",
            (uid, merged["name"], *(merged[c] for c in cols), (old or {}).get("author") or cat.author(),
             (old or {}).get("created_at") or ts, ts))
    cat.rebuild_search()
    return uid


def _row(r) -> dict[str, Any]:
    f = dict(r)
    for k in JSON_FIELDS:
        f[k] = json.loads(f[k] or ("{}" if k == "importance" else "[]"))
    f["baseline"] = json.loads(f["baseline"]) if f.get("baseline") else None
    return f


def get_feature(cat: Catalogue, uid: str | None) -> dict[str, Any] | None:
    row = cat.conn.execute("SELECT * FROM features WHERE uid = ?", (uid,)).fetchone()
    return _row(row) if row else None


def list_features(cat: Catalogue, state: str | None = "confirmed", project: str | None = None) -> list[dict[str, Any]]:
    sql, params = "SELECT * FROM features WHERE 1 = 1", []
    if state:
        sql += " AND state = ?"
        params.append(state)
    if project:
        sql += " AND project = ?"
        params.append(project)
    return [_row(r) for r in cat.conn.execute(sql + " ORDER BY project, name", params)]


def set_state(cat: Catalogue, uid: str, state: str) -> None:
    if state not in ("confirmed", "suggested", "rejected"):
        raise ValueError(state)
    with cat.conn:
        cat.conn.execute("UPDATE features SET state = ?, updated_at = ? WHERE uid = ?", (state, now(), uid))
    cat.rebuild_search()


def delete_feature(cat: Catalogue, uid: str) -> None:
    with cat.conn:
        cat.conn.execute("DELETE FROM features WHERE uid = ?", (uid,))
    cat.rebuild_search()


def models(cat: Catalogue, project: str | None = None) -> list[dict[str, Any]]:
    rows = cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind = 'model' AND status = 'active'"
                            + (" AND source = ?" if project else "") + " ORDER BY source, name",
                            (project,) if project else ()).fetchall()
    return [{"key": r["key"], "name": r["name"], "project": r["source"], **json.loads(r["attrs"])} for r in rows]


def suggest_features(cat: Catalogue) -> int:
    """Refresh suggestions from code and saved models. Your confirmed and rejected features are kept."""
    known = {(f["project"] or "", f["name"].lower()): f for f in list_features(cat, state=None)}
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for r in cat.conn.execute("SELECT key, name, source, attrs FROM assets WHERE kind IN ('script', 'notebook') "
                              "AND status = 'active'"):
        attrs = json.loads(r["attrs"])
        for f in attrs.get("feature_assignments", []):
            ident = (r["source"], f["name"].lower())
            found.setdefault(ident, {
                "name": f["name"], "project": r["source"], "built_by": f"{r['key']}::{f['function']}",
                "expression": f["expression"], "window": f.get("window"), "known_at_prediction":
                    f.get("known_at_prediction", "unknown"), "leakage_reason": f.get("leakage_reason"),
                "missing": f.get("missing"), "encoding": f.get("encoding"), "entity": f.get("entity"),
                "sources": [], "uses": f.get("uses", [])})
    model_rows = models(cat)
    for m in model_rows:
        for feat in m.get("features", []):
            ident = (m["project"], feat.lower())
            entry = found.setdefault(ident, {"name": feat, "project": m["project"], "known_at_prediction": "unknown",
                                             "sources": [], "uses": []})
            entry.setdefault("models", []).append(m["key"])
            if feat in (m.get("importance") or {}):
                entry.setdefault("importance", {})[m["key"]] = m["importance"][feat]
    # Source columns: columns with the used names in tables the building script reads.
    for ident, f in found.items():
        script = (f.get("built_by") or "").split("::")[0]
        if script and f.get("uses"):
            read = [l["dst_key"] for l in cat.links_from(script) if l["dst_key"] and l["relation"] == "reads"]
            for key in read:
                cols = {c["name"].lower(): c["name"] for c in cat.columns(key)}
                f["sources"] += [f"{key}#{cols[u.lower()]}" for u in f["uses"] if u.lower() in cols]
    n = 0
    with cat.conn:
        cat.conn.execute("DELETE FROM features WHERE state = 'suggested'")
    for ident, f in found.items():
        mine = known.get(ident)
        if mine and mine["state"] in ("confirmed", "rejected"):
            # Keep what you wrote, but bring model usage and importance up to date.
            if mine["state"] == "confirmed" and (f.get("models") or f.get("importance")):
                save_feature(cat, mine["name"], mine["uid"], models=f.get("models", []),
                             importance=f.get("importance", {}))
            continue
        f.pop("uses", None)
        save_feature(cat, f.pop("name"), state="suggested", **{k: v for k, v in f.items() if k in FIELDS})
        n += 1
    return n


def checks(cat: Catalogue, project: str | None = None) -> dict[str, list[dict[str, Any]]]:
    feats = list_features(cat, project=project)
    used_by_models: set[str] = set()
    for m in models(cat, project):
        used_by_models |= {x.lower() for x in m.get("features", [])}
    leakage = [f for f in feats if f.get("known_at_prediction") == "no"]
    unused = [f for f in feats if used_by_models and f["name"].lower() not in used_by_models and f["status"] != "retired"]
    dup: dict[str, list[dict]] = defaultdict(list)
    for f in feats:
        sig = re.sub(r"\s+", "", f.get("expression") or "") or None
        if sig:
            dup[f"{f['project']}|{sig}"].append(f)
        norm = re.sub(r"[_\s]+", "", f["name"].lower())
        dup[f"{f['project']}|name|{norm}"].append(f)
    duplicates = [g for g in dup.values() if len({x["uid"] for x in g}) > 1]
    drift = []
    for f in feats:
        if f.get("baseline") and f.get("sources"):
            d = drift_for(cat, f)
            if d:
                drift.append({"feature": f, "detail": d})
    return {"leakage": leakage, "unused": unused, "duplicates": duplicates, "drift": drift}


def set_baseline(cat: Catalogue, uid: str) -> dict[str, Any] | None:
    """Remember the source column's current profile, to compare later profiles with."""
    f = get_feature(cat, uid)
    if not f or not f["sources"]:
        return None
    key, _, col = f["sources"][0].partition("#")
    p = cat.conn.execute("SELECT * FROM profiles WHERE asset_key = ? AND column_name = ?", (key, col)).fetchone()
    if not p:
        return None
    base = {"at": now(), "source": f["sources"][0], "rows": p["row_count"], "nulls": p["null_count"],
            "mean": p["mean_value"], "percentiles": json.loads(p["percentiles"]) if p["percentiles"] else None}
    with cat.conn:
        cat.conn.execute("UPDATE features SET baseline = ? WHERE uid = ?", (json.dumps(base), uid))
    cat.changed()
    return base


def drift_for(cat: Catalogue, f: dict[str, Any]) -> str | None:
    base = f["baseline"]
    key, _, col = base["source"].partition("#")
    p = cat.conn.execute("SELECT * FROM profiles WHERE asset_key = ? AND column_name = ?", (key, col)).fetchone()
    if not p or p["profiled_at"] <= base["at"]:
        return None
    bits = []
    if base.get("rows") and p["row_count"]:
        b_null = (base["nulls"] or 0) / base["rows"]
        n_null = (p["null_count"] or 0) / p["row_count"]
        if abs(n_null - b_null) >= 0.1:
            bits.append(f"empty values {b_null:.0%} → {n_null:.0%}")
    pct = json.loads(p["percentiles"]) if p["percentiles"] else None
    bp = base.get("percentiles")
    if pct and bp and all(k in bp for k in ("p25", "p50", "p75")):
        if not (bp["p25"] <= pct["p50"] <= bp["p75"]):
            bits.append(f"median moved from {bp['p50']} to {pct['p50']} (outside the old middle half)")
    return "; ".join(bits) or None
