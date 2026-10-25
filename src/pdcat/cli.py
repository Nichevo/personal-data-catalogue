"""Command line: pdcat init | scan | search | ui."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .config import DEFAULT_HOME, EXAMPLE_CONFIG, find_config, load_config
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
    unknown = wanted - set(cfg.source_names()) - {r["name"] for r in Catalogue(cfg.db_path).folders()}
    if unknown:
        print(f"Unknown source(s): {', '.join(sorted(unknown))}. Known: {', '.join(cfg.source_names())}")
        return 2

    cat = Catalogue(cfg.db_path)
    failures = 0
    app_folders = [r for r in cat.folders() if r["name"] not in cfg.source_names()]
    # SQL Server first, so table names in code can be matched straight away.
    jobs = [(s.name, lambda s=s: scan_server(cat, s)) for s in cfg.sqlserver]
    jobs += [(f.name, lambda f=f: scan_folder(cat, f.name, f.path, f.recursive, f.max_rows)) for f in cfg.folders]
    jobs += [(r["name"], lambda r=r: scan_folder(cat, r["name"], r["path"], bool(r["recursive"]))) for r in app_folders]
    jobs += [(p.name, lambda p=p: scan_project(cat, p)) for p in cfg.python]  # last, so file links can match
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

    from .review import refresh_tasks

    review = refresh_tasks(cat)
    print(f"Review list: {review['open']} open ({review['new']} new, {review['closed']} closed automatically). "
          "See 'To review' in the app or run `pdcat review`.")
    unresolved = cat.unresolved_refs()
    if unresolved:
        print(f"{len(unresolved)} table name(s) in code did not match a scanned table "
              "(see 'Unmatched tables' in the UI).")
    print(f"Catalogue: {cfg.db_path}")
    cat.close()
    return 1 if failures else 0


def cmd_profile(args: argparse.Namespace) -> int:
    """Profile SQL tables: the ones named, or with --used, every table your code or pasted SQL uses."""
    from .scan_sqlserver import profile_tables

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
        keys += [r[0] for r in cat.conn.execute(
            """SELECT DISTINCT l.dst_key FROM links l JOIN assets a ON a.key = l.dst_key
               JOIN assets s ON s.key = l.src_key
               WHERE a.kind IN ('table', 'view') AND s.kind NOT IN ('table', 'view', 'procedure')""")]
    keys = list(dict.fromkeys(keys))
    if not keys:
        print("Nothing to profile. Name tables (e.g. dbo.SalesDaily) or use --used.")
        return 2
    failures = 0
    for src in cfg.sqlserver:
        if args.source and src.name not in args.source:
            continue
        mine = [k for k in keys if k.startswith(f"sql://{src.name.lower()}/")]
        if not mine:
            continue
        print(f"Profiling {len(mine)} table(s) on {src.name} ...", flush=True)
        try:
            failures += len(profile_tables(cat, src, mine, sample_rows=args.sample_rows))
        except Exception as e:
            failures += 1
            print(f"  failed: {type(e).__name__}: {e}")
    cat.close()
    return 1 if failures else 0


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


def cmd_ui(args: argparse.Namespace) -> int:
    cfg_path = find_config(args.config)
    cfg = load_config(cfg_path)
    app = Path(__file__).with_name("app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", str(app),
           "--server.address", "localhost", "--browser.gatherUsageStats", "false",
           "--client.toolbarMode", "minimal",
           "--", "--db", str(cfg.db_path)]
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
    s.set_defaults(fn=cmd_scan)

    pr = sub.add_parser("profile", help="measure value ranges, nulls and common values of SQL tables (read-only)")
    pr.add_argument("tables", nargs="*", help="table names like dbo.SalesDaily")
    pr.add_argument("--used", action="store_true", help="every table your code or pasted SQL uses")
    pr.add_argument("--source", action="append", help="only this SQL Server source")
    pr.add_argument("--sample-rows", type=int, default=10_000, help="rows sampled for most-common values")
    pr.set_defaults(fn=cmd_profile)

    r = sub.add_parser("review", help="list what needs your attention, most important first")
    r.add_argument("--used", action="store_true", help="only items your code uses")
    r.add_argument("--limit", type=int, default=30)
    r.set_defaults(fn=cmd_review)

    q = sub.add_parser("search", help="search the catalogue from the terminal")
    q.add_argument("text")
    q.add_argument("--kind")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(fn=cmd_search)

    e = sub.add_parser("export", help="export everything (assets, columns, links, notes) to CSV or JSON")
    e.add_argument("--format", choices=["csv", "json"], default="csv")
    e.add_argument("--out", default="catalogue-export", help="folder to write to (default: ./catalogue-export)")
    e.set_defaults(fn=cmd_export)

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
