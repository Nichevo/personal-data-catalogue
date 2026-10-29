"""A made-up SQL Server database the size of a real warehouse (415 tables, 302 views, procedures), for
checking that the after-scan steps keep up. Views and procedures are long, as real ones are: many joins,
comments, CASE expressions and nested subqueries."""
from __future__ import annotations

import random
from typing import Any

AREAS = ["Sales", "Stock", "Order", "Ship", "Plan", "Forecast", "Supplier", "Material", "Plant", "Customer",
         "Price", "Promo", "Return", "Invoice", "Warehouse", "Route", "Batch", "Quality", "Demand", "Capacity"]
SUFFIXES = ["", "Daily", "Weekly", "Hist", "Stage", "Archive", "Line", "Detail", "Summary", "Snapshot",
            "Load", "Delta", "Map", "Bak", "Old", "Raw", "Clean", "Agg", "Fact", "Dim", "Tmp"]
TYPES = [("int", 4, 10, 0), ("bigint", 8, 19, 0), ("nvarchar", 100, 0, 0), ("varchar", 50, 0, 0),
         ("decimal", 9, 18, 2), ("date", 3, 10, 0), ("datetime2", 8, 27, 7), ("bit", 1, 1, 0)]


def big_database(tables: int = 415, views: int = 302, procedures: int = 120, seed: int = 7) -> dict[str, Any]:
    rnd = random.Random(seed)
    names: list[tuple[str, str]] = []
    for i in range(tables):
        area, suffix = AREAS[i % len(AREAS)], SUFFIXES[(i // len(AREAS)) % len(SUFFIXES)]
        names.append((rnd.choice(["dbo", "dbo", "stg", "ml", "rpt"]), f"{area}{suffix}{i // 420 or ''}"))
    objects, columns, keys = [], [], []
    table_cols: dict[str, list[str]] = {}
    for schema, name in names:
        objects.append({"schema_name": schema, "object_name": name, "object_type": "U ",
                        "row_count": rnd.choice([0, 40, 12_000, 800_000, 25_000_000]), "description": None,
                        "definition": None})
        own = f"{name}Id"
        cols = [own] + rnd.sample([f"{a}Id" for a in AREAS], 6) + [f"{a}Code" for a in rnd.sample(AREAS, 3)]
        cols += [f"Col{j}" for j in range(rnd.randint(15, 60))] + ["LoadDate", "UpdatedBy", "Qty", "Amount"]
        table_cols[f"{schema}.{name}"] = cols
        for j, c in enumerate(dict.fromkeys(cols)):
            t, ml, p, s = rnd.choice(TYPES) if j else TYPES[0]
            columns.append({"schema_name": schema, "object_name": name, "column_name": c, "column_id": j + 1,
                            "type_name": t, "max_length": ml, "precision": p, "scale": s,
                            "is_nullable": j > 0, "description": None})
        keys.append({"kind": "pk", "schema_name": schema, "object_name": name, "column_name": own, "ordinal": 1,
                     "constraint_name": f"PK_{name}"})
        if name.endswith(("Line", "Detail")) and rnd.random() < 0.5:
            other = rnd.choice(names)
            keys.append({"kind": "fk", "schema_name": schema, "object_name": name, "column_name": f"{other[1]}Id",
                         "ordinal": 1, "constraint_name": f"FK_{name}_{other[1]}", "ref_schema": other[0],
                         "ref_object": other[1], "ref_column": f"{other[1]}Id"})

    def select(depth: int = 0) -> str:
        picked = rnd.sample(names, rnd.randint(3, 14))
        base = picked[0]
        lines = [f"SELECT t0.[{table_cols[f'{base[0]}.{base[1]}'][0]}],",
                 *(f"    CASE WHEN t{k}.Qty > {rnd.randint(1, 99)} THEN 'big' -- a comment with join on in it\n"
                   f"         ELSE 'small' END AS Size{k}," for k in range(len(picked))),
                 "    SUM(t0.Amount) AS Amount",
                 f"FROM [{base[0]}].[{base[1]}] AS t0 WITH (NOLOCK)"]
        for k, (schema, name) in enumerate(picked[1:], 1):
            joiner = rnd.choice(["JOIN", "LEFT JOIN", "INNER JOIN", "LEFT OUTER JOIN"])
            col = rnd.choice(table_cols[f"{schema}.{name}"][:7])
            lines.append(f"{joiner} {schema}.{name} t{k} ON t{k}.{col} = t{rnd.randrange(k)}.{col}"
                         f" AND t{k}.LoadDate >= DATEADD(day, -{rnd.randint(1, 400)}, GETDATE())")
            if depth < 2 and rnd.random() < 0.15:
                lines.append(f"LEFT JOIN ({select(depth + 1)}) sub{k} ON sub{k}.{col} = t{k}.{col}")
        lines.append("WHERE t0.LoadDate > '2020-01-01' /* filter (old rows are bad) */")
        lines.append("GROUP BY t0." + table_cols[f"{base[0]}.{base[1]}"][0])
        return "\n".join(lines)

    for i in range(views):
        body = "\nUNION ALL\n".join(select() for _ in range(rnd.choice([1, 1, 2, 4, 8])))
        objects.append({"schema_name": "rpt", "object_name": f"v{AREAS[i % len(AREAS)]}{i}", "object_type": "V ",
                        "row_count": None, "description": None,
                        "definition": f"CREATE VIEW rpt.v{AREAS[i % len(AREAS)]}{i} AS\n{body}"})
    for i in range(procedures):
        steps = []
        for _ in range(rnd.randint(2, 25)):
            target = rnd.choice(names)
            steps.append(f"INSERT INTO {target[0]}.{target[1]} ({', '.join(table_cols[f'{target[0]}.{target[1]}'][:3])})"
                         f"\n{select()};\nPRINT 'step done';")
        objects.append({"schema_name": "dbo", "object_name": f"usp_Load{i}", "object_type": "P ", "row_count": None,
                        "description": None,
                        "definition": f"CREATE PROCEDURE dbo.usp_Load{i} AS\nBEGIN\nSET NOCOUNT ON;\n"
                                      + "\n".join(steps) + "\nEND"})
    return {"objects": objects, "columns": columns, "keys": keys}
