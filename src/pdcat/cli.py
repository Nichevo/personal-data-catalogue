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
    from .scan_python import scan_project
    from .scan_sqlserver import scan_server

    cfg = load_config(find_config(args.config))
    wanted = set(args.source or [])
    unknown = wanted - set(cfg.source_names())
    if unknown:
        print(f"Unknown source(s): {', '.join(sorted(unknown))}. Known: {', '.join(cfg.source_names())}")
        return 2

    cat = Catalogue(cfg.db_path)
    failures = 0
    # SQL Server first, so table names in code can be matched straight away.
    jobs = [(s.name, scan_server, s) for s in cfg.sqlserver] + [(p.name, scan_project, p) for p in cfg.python]
    for name, fn, src in jobs:
        if wanted and name not in wanted:
            continue
        print(f"Scanning {name} ...", flush=True)
        try:
            counts = fn(cat, src)
        except Exception as e:  # keep going so one unreachable server doesn't block the rest
            failures += 1
            print(f"  failed: {type(e).__name__}: {e}")
            continue
        print("  " + ", ".join(f"{v} {k}" for k, v in counts.items()))

    unresolved = cat.unresolved_refs()
    if unresolved:
        print(f"{len(unresolved)} table name(s) in code did not match a scanned table "
              "(see 'Unmatched tables' in the UI).")
    print(f"Catalogue: {cfg.db_path}")
    cat.close()
    return 1 if failures else 0


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
