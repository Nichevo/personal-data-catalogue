import re
from pathlib import Path

import pytest

from pdcat.cli import main

COMMANDS = ["init", "scan", "profile", "review", "diagram", "search", "export", "share", "merge", "ai-export",
            "openmetadata", "ui"]


@pytest.mark.parametrize("cmd", COMMANDS)
def test_every_command_has_help_with_examples(cmd, capsys):
    with pytest.raises(SystemExit) as e:
        main([cmd, "--help"])
    assert e.value.code == 0
    text = capsys.readouterr().out
    assert "examples:" in text and f"pdcat {cmd}" in text


def test_command_reference_covers_every_command(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    listed = set(re.findall(r"^    ([a-z-]+)\s", capsys.readouterr().out, re.M))
    assert listed == set(COMMANDS)
    doc = (Path(__file__).parents[1] / "docs" / "commands.md").read_text(encoding="utf-8")
    assert all(f"### `pdcat {c}`" in doc for c in COMMANDS)
