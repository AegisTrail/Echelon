"""Version: single source of truth, CLI reporting, UA stamping."""

import re
import tomllib

import pytest

from _version import __version__


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_pyproject_reads_version_file():
    with open("pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    assert data["project"].get("dynamic") and "version" in data["project"]["dynamic"]
    assert data["tool"]["hatch"]["version"]["path"] == "_version.py"


def test_cli_reports_version(capsys):
    from echelon import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_user_agent_carries_version():
    from github_client import USER_AGENT

    assert __version__ in USER_AGENT
