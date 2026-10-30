"""The subway map: the data flow drawn as coloured lines through the scripts that use them."""
from pdcat.codemap import Graph
from pdcat.subway import layout, subway_svg


def etl() -> Graph:
    g = Graph()
    for k, kind in (("load.py", "code"), ("clean.py", "code"), ("report.pbix", "report"),
                    ("raw.csv", "file"), ("stage", "table"), ("final", "table"), ("log", "table")):
        g.nodes[k] = {"label": k, "kind": kind, "group": None, "key": k}
    for e in (("raw.csv", "load.py", "reads"), ("load.py", "stage", "writes"), ("stage", "clean.py", "reads"),
              ("clean.py", "final", "writes"), ("final", "report.pbix", "reads"),
              ("log", "clean.py", "reads"), ("clean.py", "log", "writes")):
        g.edges[e]
    return g


def test_stations_follow_run_order_and_lines_follow_the_data():
    lay = layout(etl())
    assert lay["order"] == ["load.py", "clean.py", "report.pbix"]
    lines = lay["lines"]
    assert lines["stage"].stops == ["load.py", "clean.py"] and lines["stage"].born
    assert lines["raw.csv"].stops == ["load.py"] and not lines["raw.csv"].born
    assert lines["final"].stops == ["clean.py", "report.pbix"]
    assert lines["log"].loops == {"clean.py"}  # read and written by the same script
    ys = lay["station_y"]
    assert ys["load.py"] < ys["clean.py"] < ys["report.pbix"]
    # Lines running side by side never share a track.
    for a in lines.values():
        for b in lines.values():
            if a is not b and a.track == b.track:
                assert a.bottom < b.top or b.bottom < a.top


def test_svg_links_to_items_and_survives_loops_between_scripts():
    g = etl()
    g.edges[("report.pbix", "load.py", "runs")]  # a loop: the report "runs" the first script
    svg = subway_svg(g)
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert 'href="?asset=clean.py"' in svg and "stage" in svg
