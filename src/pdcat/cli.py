"""Command line: pdcat <command>. See docs/commands.md."""
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


def _open(args: argparse.Namespace) -> Catalogue:
    return Catalogue(load_config(find_config(args.config)).db_path)


def cmd_scan(args: argparse.Namespace) -> int:
    from .scan_files import scan_folder
    from .scan_python import scan_project
    from .scan_sqlserver import scan_server
    from .scanprogress import printer

    cfg = load_config(find_config(args.config))
    wanted = set(args.source or [])
    with Catalogue(cfg.db_path) as cat:
        unknown = wanted - set(cfg.source_names()) - {r["name"] for r in cat.folders()} - {r["name"] for r in cat.libraries()}
        if unknown:
            print(f"Unknown source(s): {', '.join(sorted(unknown))}. Known: {', '.join(cfg.source_names())}")
            return 2

        if args.plan:
            from .scan_sqlserver import plan_scan
            plan = [p for s in cfg.sqlserver if not wanted or s.name in wanted for p in plan_scan(cat, s, args.database)]
            for p in plan:
                print(f"-- {p['source']} / {p['database']}: {p['purpose']}\n{p['query']};\n")
            print(f"{len(plan)} queries, sent one at a time with a pause between. Nothing was sent.")
            return 0
        failures = 0
        app_folders = [r for r in cat.folders() if r["name"] not in cfg.source_names()]
        app_libraries = [PythonSource(name=r["name"], path=Path(r["path"]), role=r["role"] or "library", data_files=False)
                         for r in cat.libraries() if r["name"] not in cfg.source_names()]
        # SQL Server first, so table names in code can be matched straight away.
        jobs = [(s.name, lambda s=s: scan_server(cat, s, args.database)) for s in cfg.sqlserver]
        report = printer()
        jobs += [(f.name, lambda f=f: scan_folder(cat, f.name, f.path, f.recursive, f.max_rows, report))
                 for f in cfg.folders]
        jobs += [(r["name"], lambda r=r: scan_folder(cat, r["name"], r["path"], bool(r["recursive"]), report=report))
                 for r in app_folders]
        jobs += [(p.name, lambda p=p: scan_project(cat, p, report)) for p in [*app_libraries, *cfg.python]]  # last, so file links can match
        from .scan_files import scanned_recently
        for name, run in jobs:
            if wanted and name not in wanted:
                continue
            ago = None if args.force else scanned_recently(cat, name)
            if ago:
                print(f"Skipping {name}: scanned {ago} (use --force to scan it anyway)")
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

        print("Working out suggestions and the review list ...", flush=True)
        done = after_scan(cat, progress=print, report=printer())
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
    return 1 if failures else 0


def cmd_profile(args: argparse.Namespace) -> int:
    """Profile SQL tables at the controlled pace: the ones named, every table your code uses (--used),
    or whatever is approved in the app's queue (--queue). --plan prints the queries without connecting."""
    from .pacing import plan_profile, queue, request_profiles, used_tables
    from .scan_sqlserver import run_queue

    cfg = load_config(find_config(args.config))
    with Catalogue(cfg.db_path) as cat:
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
            return 0
        if keys:
            request_profiles(cat, keys, approve=True)  # naming them here is the approval
        elif not args.queue:
            print("Nothing to profile. Name tables (e.g. dbo.SalesDaily), use --used, or --queue for the app's queue.")
            return 2
        result = run_queue(cat, cfg.sqlserver)
    print(f"Profiled {result['done']} table(s), {result['failed']} failed, {result['left']} still waiting.")
    if result["stopped"]:
        print(f"Stopped early: {result['stopped']}")
    return 1 if result["failed"] else 0


def cmd_review(args: argparse.Namespace) -> int:
    from .review import RULES, open_tasks, refresh_tasks

    with _open(args) as cat:
        refresh_tasks(cat)
        tasks = open_tasks(cat, only_used=args.used, limit=args.limit)
    for t in tasks:
        print(f"[{RULES[t['rule']][0]}] {t['title']}" + (f"\n    {t['detail']}" if t["detail"] else ""))
    if not tasks:
        print("Nothing to review.")
    return 0


def cmd_diagram(args: argparse.Namespace) -> int:
    from .codemap import build_graph, to_dot, to_mermaid

    with _open(args) as cat:
        g = build_graph(cat, args.project, level=args.level, include_data=not args.no_data)
    text = to_mermaid(g) if args.format == "mermaid" else to_dot(g)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out} ({len(g.nodes)} nodes, {len(g.edges)} links)")
    else:
        print(text)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    with _open(args) as cat:
        rows = cat.search(args.text, kind=args.kind, limit=args.limit)
    for r in rows:
        desc = (r["description"] or "").splitlines()[0][:80] if r["description"] else ""
        notes = f"  [{r['note_count']} note(s)]" if r["note_count"] else ""
        print(f"{r['kind']:<12} {r['name']}{notes}\n             {r['key']}" + (f"\n             {desc}" if desc else ""))
    if not rows:
        print("No matches.")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .export import write_export

    with _open(args) as cat:
        for path in write_export(cat, Path(args.out), args.format):
            print(f"Wrote {path}")
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    from .sharing import to_share_json

    with _open(args) as cat:
        Path(args.out).write_text(to_share_json(cat, args.scan or []), encoding="utf-8")
    print(f"Wrote {args.out}. Send it to a colleague to merge with `pdcat merge`.")
    return 0


def cmd_merge(args: argparse.Namespace) -> int:
    from .sharing import merge, preview, read_share, suggest_mapping

    with _open(args) as cat:
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
    return 0


def cmd_ai_export(args: argparse.Namespace) -> int:
    from .aipack import write

    with _open(args) as cat:
        files = write(cat, Path(args.out), hide_locations=args.hide_locations, include_values=args.values,
                      fmt="docx" if args.docx else "md", include_documents=args.documents)
    print(f"Wrote {len(files)} files to {args.out}. Put the folder on OneDrive or SharePoint and add it to a "
          "Copilot agent as knowledge; agent-instructions has the text to paste.")
    return 0


def cmd_openmetadata(args: argparse.Namespace) -> int:
    from .openmeta import to_zip

    with _open(args) as cat:
        Path(args.out).write_bytes(to_zip(cat))
    print(f"Wrote {args.out}. Unzip it and run load_openmetadata.py against your OpenMetadata server.")
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


KINDS = ("table, view, procedure, database, script, project, model, data_file, file_series, folder, "
         "report, measure, workbook, sql_snippet")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pdcat", description="Personal data catalogue. Run `pdcat <command> --help` for a command's options "
        "and examples, or see docs/commands.md.",
        epilog="Most people only need `pdcat ui`; the app has buttons for all of these commands.")
    p.add_argument("--config", help="path to config.toml (default: ./config.toml, then ~/.pdcat/config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="<command>")

    def command(name: str, summary: str, examples: str, fn) -> argparse.ArgumentParser:
        c = sub.add_parser(name, help=summary, description=summary[0].upper() + summary[1:] + ".",
                           epilog="examples:\n" + examples, formatter_class=argparse.RawDescriptionHelpFormatter)
        c.set_defaults(fn=fn)
        return c

    command("init", "create a starter config file in ~/.pdcat (or at --config)",
            "  pdcat init\n  pdcat --config C:\\work\\config.toml init", cmd_init)

    s = command("scan", "scan your databases, projects, data folders, reports and workbooks",
                "  pdcat scan                       everything in the config\n"
                "  pdcat scan --source forecasting  one source (use the name you gave it)\n"
                "  pdcat scan --source warehouse --database Sales\n"
                "  pdcat scan --plan                show the SQL Server queries first, send nothing", cmd_scan)
    s.add_argument("--source", action="append", metavar="NAME", help="only scan this source (repeatable)")
    s.add_argument("--database", action="append", metavar="DB", help="only this SQL Server database (repeatable)")
    s.add_argument("--plan", action="store_true", help="print the SQL Server queries that would run, without connecting")
    s.add_argument("--force", action="store_true", help="scan sources even if they were scanned in the last hour")

    pr = command("profile", "measure value ranges, nulls and common values of SQL tables (read-only, paced)",
                 "  pdcat profile dbo.SalesDaily dbo.Stores\n"
                 "  pdcat profile --used             every table your code uses\n"
                 "  pdcat profile --queue            the tables approved on the app's SQL scanning page\n"
                 "  pdcat profile --used --plan      show the queries, send nothing", cmd_profile)
    pr.add_argument("tables", nargs="*", metavar="TABLE", help="table names like dbo.SalesDaily")
    pr.add_argument("--used", action="store_true", help="every table your code or pasted SQL uses")
    pr.add_argument("--source", action="append", metavar="NAME", help="only this SQL Server source (repeatable)")
    pr.add_argument("--queue", action="store_true", help="run the tables approved in the app's queue")
    pr.add_argument("--plan", action="store_true", help="print the queries that would run, without connecting")

    r = command("review", "list what needs your attention, most important first",
                "  pdcat review\n  pdcat review --used --limit 10", cmd_review)
    r.add_argument("--used", action="store_true", help="only items your code uses")
    r.add_argument("--limit", type=int, default=30, metavar="N", help="show at most N items (default: 30)")

    d = command("diagram", "write a flow diagram of how your projects, libraries and data connect",
                "  pdcat diagram --out map.mmd\n"
                "  pdcat diagram --project forecasting --level projects --out map.mmd\n"
                "  pdcat diagram --format dot --no-data --out code.dot", cmd_diagram)
    d.add_argument("--project", metavar="NAME", help="start from this project or library (default: everything)")
    d.add_argument("--level", choices=["modules", "projects"], default="modules",
                   help="one box per script (modules, the default) or per project")
    d.add_argument("--no-data", action="store_true", help="code only, without tables and files")
    d.add_argument("--format", choices=["mermaid", "dot"], default="mermaid",
                   help="mermaid (default; pastes into GitHub, Azure DevOps, Confluence) or dot (Graphviz)")
    d.add_argument("--out", metavar="FILE", help="file to write (default: print it)")

    q = command("search", "search the catalogue from the terminal",
                '  pdcat search "store sales"\n  pdcat search plant --kind table --limit 5', cmd_search)
    q.add_argument("text", help="words to look for, in names, columns, descriptions, notes and code")
    q.add_argument("--kind", help=f"only this kind of item: {KINDS}")
    q.add_argument("--limit", type=int, default=20, metavar="N", help="show at most N matches (default: 20)")

    e = command("export", "export everything (items, columns, links, notes, profiles) to CSV or JSON",
                "  pdcat export\n  pdcat export --format json --out C:\\temp\\catalogue", cmd_export)
    e.add_argument("--format", choices=["csv", "json"], default="csv", help="csv (default, one file per table) or json")
    e.add_argument("--out", default="catalogue-export", metavar="FOLDER",
                   help="folder to write to (default: ./catalogue-export)")

    sh = command("share", "write your notes and knowledge to a file a colleague can merge",
                 "  pdcat share --out mine.json\n  pdcat share --out mine.json --scan warehouse", cmd_share)
    sh.add_argument("--out", default="catalogue-share.json", metavar="FILE",
                    help="file to write (default: catalogue-share.json)")
    sh.add_argument("--scan", action="append", metavar="SOURCE",
                    help="also include your scan of this source, for a colleague who can't scan it (repeatable)")

    mg = command("merge", "merge a colleague's share file into your catalogue (undo it on the app's Share page)",
                 "  pdcat merge theirs.json --preview\n"
                 "  pdcat merge theirs.json --map dw=warehouse\n"
                 "  pdcat merge theirs.json --take-theirs", cmd_merge)
    mg.add_argument("file", help="the .json file they sent")
    mg.add_argument("--map", action="append", metavar="THEIRS=YOURS",
                    help="their source name = yours, when the names differ, e.g. dw=warehouse (repeatable)")
    mg.add_argument("--take-theirs", action="store_true", help="where you both wrote something different, use theirs")
    mg.add_argument("--preview", action="store_true", help="show what would change, change nothing")

    ai = command("ai-export", "write the Copilot pack: what the catalogue knows, as documents for an agent",
                 "  pdcat ai-export --out C:\\temp\\copilot-pack\n"
                 "  pdcat ai-export --docx --hide-locations", cmd_ai_export)
    ai.add_argument("--out", default="copilot-pack", metavar="FOLDER", help="folder to write to (default: ./copilot-pack)")
    ai.add_argument("--docx", action="store_true", help="Word files instead of Markdown")
    ai.add_argument("--hide-locations", action="store_true", help="leave out server names and file locations")
    ai.add_argument("--values", action="store_true", help="include value ranges and common values (never sensitive)")
    ai.add_argument("--documents", action="store_true", help="include the text of reference documents")

    om = command("openmetadata", "export tables, descriptions, glossary and lineage for OpenMetadata",
                 "  pdcat openmetadata --out om.zip", cmd_openmetadata)
    om.add_argument("--out", default="openmetadata-export.zip", metavar="FILE",
                    help="zip to write (default: openmetadata-export.zip)")

    u = command("ui", "open the catalogue in your browser", "  pdcat ui\n  pdcat ui --port 8600", cmd_ui)
    u.add_argument("--port", type=int, help="port to use (default: Streamlit's, 8501)")

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
