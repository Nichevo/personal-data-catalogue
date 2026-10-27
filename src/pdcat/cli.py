"""Command line: pdcat init | scan | search | ui."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_HOME, EXAMPLE_CONFIG, PythonSource, find_config, load_config
from .store import Catalogue


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config).expanduser() if args.config else DEFAULT_HOME / "config.toml"
    if path.exists():
        print(f"Config already exists: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
        print(f"Created {path}")
    print("Edit it to list your SQL Server databases and Python project folders, then run `pdcat scan`.")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .scan_files import scan_folder
    from .scan_python import scan_project
    from .scan_sqlserver import scan_server

    cfg = load_config(find_config(args.config))
    wanted = set(args.source or [])
    _cat = Catalogue(cfg.db_path)
    unknown = wanted - set(cfg.source_names()) - {r["name"] for r in _cat.folders()} - {r["name"] for r in _cat.libraries()}
    _cat.close()
    if unknown:
        print(f"Unknown source(s): {', '.join(sorted(unknown))}. Known: {', '.join(cfg.source_names())}")
        return 2

    cat = Catalogue(cfg.db_path)
    if args.plan:
        from .scan_sqlserver import plan_scan
        plan = [p for s in cfg.sqlserver if not wanted or s.name in wanted for p in plan_scan(cat, s, args.database)]
        for p in plan:
            print(f"-- {p['source']} / {p['database']}: {p['purpose']}\n{p['query']};\n")
        print(f"{len(plan)} queries, sent one at a time with a pause between. Nothing was sent.")
        cat.close()
        return 0
    failures = 0
    app_folders = [r for r in cat.folders() if r["name"] not in cfg.source_names()]
    app_libraries = [PythonSource(name=r["name"], path=Path(r["path"]), role="library", data_files=False)
                     for r in cat.libraries() if r["name"] not in cfg.source_names()]
    # SQL Server first, so table names in code can be matched straight away.
    jobs = [(s.name, lambda s=s: scan_server(cat, s, args.database)) for s in cfg.sqlserver]
    jobs += [(f.name, lambda f=f: scan_folder(cat, f.name, f.path, f.recursive, f.max_rows)) for f in cfg.folders]
    jobs += [(r["name"], lambda r=r: scan_folder(cat, r["name"], r["path"], bool(r["recursive"]))) for r in app_folders]
    jobs += [(p.name, lambda p=p: scan_project(cat, p)) for p in [*app_libraries, *cfg.python]]  # last, so file links can match
    for name, run in jobs:
        if wanted and name not in wanted:
            continue
        print(f"Scanning {name} ...", flush=True)
        try:
            counts = run()
        except Exception as e:  # keep going so one unreachable server doesn't block the rest
            failures += 1
            print(f"  failed: {type(e).__name__}: {e}")
            continue
        print("  " + ", ".join(f"{v} {k}" for k, v in counts.items()))

    from .postscan import after_scan

    done = after_scan(cat, progress=print)
    review = {k.removeprefix("review "): v for k, v in done.items() if k.startswith("review ")}
    print(f"Review list: {review['open']} open ({review['new']} new, {review['closed']} closed automatically). "
          "See 'To review' in the app or run `pdcat review`.")
    from .codemap import uncatalogued_libraries

    for lib in uncatalogued_libraries(cat):
        if lib["path"]:
            print(f"Home-built library '{lib['name']}' (used by {lib['used_by']} file(s)) isn't catalogued. "
                  f"It looks like it lives in {lib['path']} ({lib['how']}). Add it on the Code map page "
                  "or as a [[python]] block with role = \"library\".")
    unresolved = cat.unresolved_refs()
    if unresolved:
        print(f"{len(unresolved)} table name(s) in code did not match a scanned table "
              "(see 'Unmatched tables' in the UI).")
    print(f"Catalogue: {cfg.db_path}")
    cat.close()
    return 1 if failures else 0


def cmd_profile(args: argparse.Namespace) -> int:
    """Profile SQL tables at the controlled pace: the ones named, every table your code uses (--used),
    or whatever is approved in the app's queue (--queue). --plan prints the queries without connecting."""
    from .pacing import plan_profile, queue, request_profiles, used_tables
    from .scan_sqlserver import run_queue

    cfg = load_config(find_config(args.config))
    cat = Catalogue(cfg.db_path)
    keys: list[str] = []
    for name in args.tables:
        matches = [r["key"] for r in cat.conn.execute(
            "SELECT key FROM assets WHERE kind IN ('table', 'view') AND (lower(name) = lower(?) OR key = lower(?))",
            (name, name))]
        if not matches:
            print(f"No scanned table called {name}")
        keys += matches
    if args.used:
        keys += used_tables(cat)
    if args.source:
        keys = [k for k in keys if any(k.startswith(f"sql://{s.lower()}/") for s in args.source)]
    keys = list(dict.fromkeys(keys))
    if args.plan:
        plan = plan_profile(cat, keys or [q["asset_key"] for q in queue(cat, ("approved",))])
        for p in plan:
            print(f"-- {p['table']}: {p['purpose']}\n{p['query']};\n")
        print(f"{len(plan)} queries, sent one at a time. Nothing was sent.")
        cat.close()
        return 0
    if keys:
        request_profiles(cat, keys, approve=True)  # naming them here is the approval
    elif not args.queue:
        print("Nothing to profile. Name tables (e.g. dbo.SalesDaily), use --used, or --queue for the app's queue.")
        cat.close()
        return 2
    result = run_queue(cat, cfg.sqlserver)
    print(f"Profiled {result['done']} table(s), {result['failed']} failed, {result['left']} still waiting.")
    if result["stopped"]:
        print(f"Stopped early: {result['stopped']}")
    cat.close()
    return 1 if result["failed"] else 0


def cmd_review(args: argparse.Namespace) -> int:
    from .review import RULES, open_tasks, refresh_tasks

    cfg = load_config(find_config(args.config))
    cat = Catalogue(cfg.db_path)
    refresh_tasks(cat)
    tasks = open_tasks(cat, only_used=args.used, limit=args.limit)
    for t in tasks:
        print(f"[{RULES[t['rule']][0]}] {t['title']}" + (f"\n    {t['detail']}" if t["detail"] else ""))
    if not tasks:
        print("Nothing to review.")
    cat.close()
    return 0


def cmd_diagram(args: argparse.Namespace) -> int:
    from .codemap import build_graph, to_dot, to_mermaid

    cfg = load_config(find_config(args.config))
    cat = Catalogue(cfg.db_path)
    g = build_graph(cat, args.project, level=args.level, include_data=not args.no_data)
    text = to_mermaid(g) if args.format == "mermaid" else to_dot(g)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out} ({len(g.nodes)} nodes, {len(g.edges)} links)")
    else:
        print(text)
    cat.close()
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    cfg = load_config(find_config(args.config))
    cat = Catalogue(cfg.db_path)
    rows = cat.search(args.text, kind=args.kind, limit=args.limit)
    for r in rows:
        desc = (r["description"] or "").splitlines()[0][:80] if r["description"] else ""
        notes = f"  [{r['note_count']} note(s)]" if r["note_count"] else ""
        print(f"{r['kind']:<10} {r['name']}{notes}\n           {r['key']}" + (f"\n           {desc}" if desc else ""))
    if not rows:
        print("No matches.")
    cat.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .export import write_export

    cfg = load_config(find_config(args.config))
    cat = Catalogue(cfg.db_path)
    for path in write_export(cat, Path(args.out), args.format):
        print(f"Wrote {path}")
    cat.close()
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    from .sharing import to_share_json

    cat = Catalogue(load_config(find_config(args.config)).db_path)
    Path(args.out).write_text(to_share_json(cat, args.scan or []), encoding="utf-8")
    print(f"Wrote {args.out}. Send it to a colleague to merge with `pdcat merge`.")
    cat.close()
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    from .sharing import merge, preview, read_share, suggest_mapping

    cat = Catalogue(load_config(find_config(args.config)).db_path)
    pack = read_share(Path(args.file).read_text(encoding="utf-8"))
    mapping = suggest_mapping(cat, pack)
    for m in args.map or []:
        theirs, _, mine = m.partition("=")
        mapping[theirs.strip()] = mine.strip()
    p = preview(cat, pack, mapping)
    print(f"From {pack.get('author') or 'someone'} ({pack.get('made_at', '')[:10]}). Their sources: "
          + ", ".join(f"{k} -> {v}" for k, v in mapping.items()))
    for table, c in p["sections"].items():
        if any(c.values()):
            print(f"  {table}: {c['new']} new, {c['fills']} fill gaps, {c['different']} different, {c['same']} same")
    for source, s in p["scans"].items():
        print(f"  scan of {source} into {s['into']}: {s['new']} of {s['items']} items new")
    if args.preview:
        print("Preview only: nothing was changed.")
        return 0
    result = merge(cat, pack, mapping, take_theirs=args.take_theirs, file_name=Path(args.file).name)
    print(f"Merged as batch {result['batch']}. Undo it on the app's Share page.")
    cat.close()
    return 0


def cmd_ai_export(args: argparse.Namespace) -> int:
    from .aipack import write

    cat = Catalogue(load_config(find_config(args.config)).db_path)
    files = write(cat, Path(args.out), hide_locations=args.hide_locations, include_values=args.values,
                  fmt="docx" if args.docx else "md")
    print(f"Wrote {len(files)} files to {args.out}. Put the folder on OneDrive or SharePoint and add it to a "
          "Copilot agent as knowledge; agent-instructions has the text to paste.")
    cat.close()
    return 0


def cmd_openmetadata(args: argparse.Namespace) -> int:
    from .openmeta import to_zip

    cat = Catalogue(load_config(find_config(args.config)).db_path)
    Path(args.out).write_bytes(to_zip(cat))
    print(f"Wrote {args.out}. Unzip it and run load_openmetadata.py against your OpenMetadata server.")
    cat.close()
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .firstrun import ensure_config
    cfg_path = find_config(args.config)
    if not cfg_path.exists():  # first run: the app's Get started page fills it in
        ensure_config(cfg_path)
        print(f"Created {cfg_path}")
    cfg = load_config(cfg_path)
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    app = Path(__file__).with_name("app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app),
           "--server.address", "localhost", "--browser.gatherUsageStats", "false",
           "--client.toolbarMode", "minimal",
           "--", "--db", str(cfg.db_path), "--config", str(cfg_path)]
    if args.port:
        cmd[5:5] = ["--server.port", str(args.port)]
    return subprocess.call(cmd)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="pdcat", description="Personal data catalogue")
    p.add_argument("--config", help="path to config.toml (default: ./config.toml, then ~/.pdcat/config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create a starter config file").set_defaults(fn=cmd_init)

    s = sub.add_parser("scan", help="scan your databases and projects")
    s.add_argument("--source", action="append", help="only scan this source (repeatable)")
    s.add_argument("--database", action="append", help="only this SQL Server database (repeatable)")
    s.add_argument("--plan", action="store_true", help="print the SQL Server queries that would run, without connecting")
    s.set_defaults(fn=cmd_scan)

    pr = sub.add_parser("profile", help="measure value ranges, nulls and common values of SQL tables (read-only)")
    pr.add_argument("tables", nargs="*", help="table names like dbo.SalesDaily")
    pr.add_argument("--used", action="store_true", help="every table your code or pasted SQL uses")
    pr.add_argument("--source", action="append", help="only this SQL Server source")
    pr.add_argument("--queue", action="store_true", help="run the tables approved in the app's queue")
    pr.add_argument("--plan", action="store_true", help="print the queries that would run, without connecting")
    pr.set_defaults(fn=cmd_profile)

    r = sub.add_parser("review", help="list what needs your attention, most important first")
    r.add_argument("--used", action="store_true", help="only items your code uses")
    r.add_argument("--limit", type=int, default=30)
    r.set_defaults(fn=cmd_review)

    d = sub.add_parser("diagram", help="flow diagram of how your projects, libraries and data connect")
    d.add_argument("--project", help="start from this project or library (default: everything)")
    d.add_argument("--level", choices=["modules", "projects"], default="modules")
    d.add_argument("--no-data", action="store_true", help="code only, without tables and files")
    d.add_argument("--format", choices=["mermaid", "dot"], default="mermaid")
    d.add_argument("--out", help="file to write (default: print)")
    d.set_defaults(fn=cmd_diagram)

    q = sub.add_parser("search", help="search the catalogue from the terminal")
    q.add_argument("text")
    q.add_argument("--kind")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(fn=cmd_search)

    e = sub.add_parser("export", help="export everything (assets, columns, links, notes) to CSV or JSON")
    e.add_argument("--format", choices=["csv", "json"], default="csv")
    e.add_argument("--out", default="catalogue-export", help="folder to write to (default: ./catalogue-export)")
    e.set_defaults(fn=cmd_export)

    sh = sub.add_parser("share", help="write your notes and knowledge to a file a colleague can merge")
    sh.add_argument("--out", default="catalogue-share.json")
    sh.add_argument("--scan", action="append", help="also include your scan of this source (repeatable)")
    sh.set_defaults(fn=cmd_share)

    mg = sub.add_parser("merge", help="merge a colleague's share file into your catalogue")
    mg.add_argument("file")
    mg.add_argument("--map", action="append", help="their source = yours, e.g. dw=warehouse (repeatable)")
    mg.add_argument("--take-theirs", action="store_true", help="where you both wrote something different, use theirs")
    mg.add_argument("--preview", action="store_true", help="show what would change, change nothing")
    mg.set_defaults(fn=cmd_merge)

    ai = sub.add_parser("ai-export", help="write the Copilot pack: what the catalogue knows, as documents")
    ai.add_argument("--out", default="copilot-pack")
    ai.add_argument("--docx", action="store_true", help="Word files instead of Markdown")
    ai.add_argument("--hide-locations", action="store_true", help="leave out server names and file locations")
    ai.add_argument("--values", action="store_true", help="include value ranges and common values (never sensitive)")
    ai.set_defaults(fn=cmd_ai_export)

    om = sub.add_parser("openmetadata", help="export tables, descriptions, glossary and lineage for OpenMetadata")
    om.add_argument("--out", default="openmetadata-export.zip")
    om.set_defaults(fn=cmd_openmetadata)

    u = sub.add_parser("ui", help="open the catalogue in your browser")
    u.add_argument("--port", type=int)
    u.set_defaults(fn=cmd_ui)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # odd characters in names must not crash a Windows console
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError) as e:
        print(e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
