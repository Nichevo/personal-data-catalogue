# Lessons from the version 2.11 code review

Version 2.11 is a tidy-up of code that was written quickly, feature by feature, over versions
2.0 and 2.1. This note records what the review found, what was done about it, and what the
same patterns would mean for any other project built the same way. The findings are listed
from the most useful habit to the least.

## 1. Run a linter from day one

`ruff check` on the 2.1 code found about 150 things straight away: unused imports, unused
variables, f-strings with nothing in them, trailing whitespace, `typing.Callable` where
`collections.abc.Callable` is current, and a method with `@functools.lru_cache` on it (which
keeps every instance alive for the life of the process). None of them was a bug on its own,
but each one is noise that hides a real mistake, and all of them were a one-line fix when
found early.

**For other projects:** add `[tool.ruff]` to `pyproject.toml` and a lint step to CI in the
first commit. The rule set here is small on purpose (`F`, `E9`, `W`, `I`, `B`, `UP`, `SIM`,
`PLE`, `PLW`) and every ignored rule has a comment saying why. Ignoring a rule with a reason is
fine; not running the linter is how the list grows to 150.

## 2. Registries built from strings hide broken features

The post-scan finders, review checks and suggestion handlers were each wired up through a
list of module names, imported with `importlib.import_module` and `getattr`, wrapped in
`except ImportError: continue`. That shape is attractive while adding features one at a
time, because a half-written module never breaks the scan. It also means a renamed function,
a typo in the list, or a module that fails to import makes the feature silently disappear.
A review check that raised was swallowed by `except Exception: continue`, so a broken check
emptied part of the review list with no trace.

**For other projects:** import the functions directly and put them in a plain list. If one
step must not stop the others, catch the error at the call site and *report* it
(`warnings.warn`, a log line, a count of failures in the result) rather than `continue`. Use
`importlib` only for things that are genuinely optional, and say so in the message when they
are missing, as `sqlglot` and `pypdf` already do here.

## 3. Names that say when something was written go stale

`aipack21.py`, `drafts2.py`, `V21_STEPS`, `_v2_candidates`, `_v21_candidates` and
`test_v21_*.py` all name the release they were added in, not what they do. Six months on,
nobody will know or care that the SAP section of the Copilot pack arrived in 2.1, and
`backups.py` (main and backup table pairs) sat next to `backup.py` (copies of the catalogue
file) with nothing to tell them apart.

**For other projects:** name modules and functions after their job (`tablepairs`,
`commentdrafts`, `FEATURE_CHECKS`). When a new release extends an existing module, extend
it; a `thing2.py` beside `thing.py` is a sign the first file needed a tidy, not a sibling.
Release numbers belong in the README and the changelog.

## 4. Optional-dependency fallbacks spread further than they need to

Because the app has to run without `pyarrow`, `sqlglot`, `pypdf` and `pywin32`, the
`try: import … except ImportError` pattern became the default way to import anything,
including the project's own modules (`from .series import series_files` was wrapped in
one). A fallback around an internal import only ever hides a mistake.

**For other projects:** keep the fallback at the single place that needs it (one import in
one module, with a `HAVE_X` flag or a helper that explains what to install), and import
everything else plainly.

## 5. The same thing written three ways

The UTF-8 byte-order mark that makes Excel open CSV files correctly was written as
`"﻿"`, as the invisible character itself, and as an escape, in four places. The
`--db`/`--config` arguments were parsed twice in two functions. The catalogue was opened
and closed by hand in eleven CLI commands, twice in one of them, and once not at all on an
early return. Each case is harmless alone and costly together, because the next person has
to work out whether the three spellings are meant to differ.

**For other projects:** when a value or a sequence of calls appears a third time, give it a
name (`BOM`, `_app_args()`, `with Catalogue(path) as cat:`). Context managers for anything
that is opened and closed remove a whole class of early-return leaks.

## 6. Dead code accumulates when features are built in a hurry

Ten functions had no caller anywhere in the source, tests or scripts: helpers written for
a page that was later merged into another, a second way to do something the first way
already covered, and an `__all__` list that existed only to silence an unused-import
warning. A quick way to find them is `vulture src` plus a word search for each name, keeping
anything reached through a registry or a string.

**For other projects:** when a page or feature is reworked, delete what it replaced in the
same change. Unused code is not free: it still has to be read, and it still breaks when its
dependencies change.

## 7. Where the version number lives

The version was in two places (`pyproject.toml` and `pdcat.__version__`) that had to be
kept in step by hand, and the upgrade message in the app had its own copy of the number.
`pyproject.toml` now reads it from the package (`dynamic = ["version"]`), and the message
uses `__version__`.

## 8. Tests were good; CI was missing

The project had 300 tests covering most behaviour, including a run with `pyarrow`
deliberately broken, but no workflow ran them on a push. The only CI was the Windows
release build. A `tests.yml` that installs the package and runs `ruff` and `pytest` on
Linux and Windows now runs on every push and pull request, so a change that breaks the
Windows-specific paths (Access via DAO, scheduled tasks, Excel macros) is caught before a
release.

**For other projects:** CI is a ten-line file. Add it with the first test.

## Two real bugs the review found

Neither was reported; both were found by reading code with a linter's eye.

- Reference documents appeared as `doc://<uid>` in search results and in the Home page's
  recently viewed list, because the display-name lookup had every other kind of key but not
  documents (added in 2.1 after the lookup was written). A test now covers it.
- A project's data files were catalogued even when they sat under a folder marked *ignore*
  in Folder roles, while data folders honoured the same marking. Both paths now use the same
  filter.

Both are the second pattern again: a feature added at the edge of the system without
walking back to the places that enumerate "every kind of thing".

## 9. A long job with no feedback looks broken (2.12)

A folder scan was reported as "stuck". Nothing had hung for good, but every slow part was
silent: the scan sat under one spinner, the two dozen post-scan steps ran after the spinner
had gone, the folder was walked four times (once per file type, plus once more for code),
the walk went into `.git`, `node_modules` and conda environments, a big `.pbix` was unpacked
by a third-party library with no time limit, and an Excel workbook full of data had all of
its shared strings parsed just to read a header row.

The fix was mostly plumbing: one walk that steps around tool folders, a `report(text, done,
total)` function passed down to every scanner, a progress box in the app that names the file
being read and mentions any that take long, the same updates in the terminal, and the
third-party parser run in its own process with a timeout.

The same release fixed "database is locked". SQLite allows one writer at a time, and each
scan wrote inside a single transaction (good: a failed scan changes nothing) while it also
read every file (bad: minutes with the lock held). Worse, merely opening the catalogue ran
migration `UPDATE`s that matched no rows but still needed the write lock. Files are now read
before the transaction opens, migrations write only when there is something to change, and
the busy timeout is a minute rather than five seconds.

**For other projects:** give every long-running job a progress callback from day one, even if
the first caller ignores it. Run libraries you don't control in a subprocess with a timeout
when they chew on user files. Walk a file tree once and prune what you will never want. With SQLite, do slow work outside
the write transaction, and never write on open unless something actually needs changing.
