"""One source for the help text, the shell completions and the man page: the click commands of
``fsbpyr.cli`` (D40). The README's option table is checked against them."""

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.shell_completion import ShellComplete

from fsbpyr.build import TARGET_PYTHON
from fsbpyr.cli import EXAMPLES, cli, main
from fsbpyr.config import load_config
from fsbpyr.project import Project
from fsbpyr.runtime import pins

from ..support.readme import quickstart

ROOT = Path(__file__).parents[2]
PYPROJECT = """\
[project]
name = "app"
version = "1"

[project.scripts]
mytool = "app.cli:main"
other = "app.other:main"

[project.optional-dependencies]
fancy = []
fast = []
"""


def _complete(args: list[str], incomplete: str) -> list[str]:
    completion = ShellComplete(cli, {}, "fsbpyr", "_FSBPYR_COMPLETE")
    return [item.value for item in completion.get_completions(args, incomplete)]


def _commands(ctx: click.Context) -> Iterator[click.Context]:
    """Every command below the group of ``ctx``, as a context of its own."""
    group = ctx.command
    assert isinstance(group, click.Group)
    for name in group.list_commands(ctx):
        command = group.get_command(ctx, name)
        assert command is not None
        sub = click.Context(command, info_name=name, parent=ctx)
        yield sub
        if isinstance(command, click.Group):
            yield from _commands(sub)


def _option_rows(command: click.Command) -> list[str]:
    ctx = click.Context(command, info_name=command.name)
    help_option = command.get_help_option(ctx)
    records = [param.get_help_record(ctx) for param in command.get_params(ctx)
               if param is not help_option]  # fmt: skip
    return [f"| `{flags}` | {text} |" for flags, text in filter(None, records)]


# --- completion ------------------------------------------------------------------------------


def test_completion_offers_commands_and_both_forms_of_on_off_options() -> None:
    assert _complete([], "") == ["build", "cache", "completion", "inspect", "man", "runtime"]
    assert _complete(["build"], "--no-") == [
        "--no-offline", "--no-source", "--no-fast-compression", "--no-best-compression",
        "--no-hints", "--no-interpreter-reexec", "--no-smoke",
    ]  # fmt: skip


def test_entry_completes_the_console_scripts_of_the_current_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    monkeypatch.chdir(tmp_path)

    assert _complete(["build", "--entry"], "") == ["mytool", "other"]
    assert _complete(["build", "--entry"], "my") == ["mytool"]


def test_extra_completes_the_extras_of_the_named_project(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)

    assert _complete(["build", str(tmp_path), "--extra"], "") == ["fancy", "fast"]
    assert _complete(["build", str(tmp_path), "--extra", "fancy", "--extra"], "fas") == ["fast"]


def test_entry_completes_in_the_option_equals_value_form(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)

    assert _complete(["build", str(tmp_path)], "--entry=my") == ["mytool"]


UNREADABLE: dict[str, bytes | None] = {
    "none": None,
    "broken": b"[project\n",
    "not-utf8": b'[project]\nname = "\xff"\n',
    "scripts-string": b'[project]\nname = "app"\nscripts = "x"\n',
}


@pytest.mark.parametrize("pyproject", UNREADABLE.values(), ids=UNREADABLE.keys())
def test_project_values_complete_to_nothing_without_a_readable_project(
    tmp_path: Path, pyproject: bytes | None
) -> None:
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_bytes(pyproject)

    assert _complete(["build", str(tmp_path), "--entry"], "") == []
    assert _complete(["build", str(tmp_path), "--extra"], "") == []


def test_completion_prints_no_errors_for_an_unreadable_project(tmp_path: Path) -> None:
    """Through the shell protocol, as a script runs it: anything on stderr lands on the prompt."""
    (tmp_path / "pyproject.toml").write_bytes(b'[project]\nname = "\xff"\n')  # not UTF-8
    env = {**os.environ, "_FSBPYR_COMPLETE": "bash_complete",
           "COMP_WORDS": "fsbpyr build --entry ", "COMP_CWORD": "3"}  # fmt: skip

    result = subprocess.run([sys.executable, "-m", "fsbpyr"], cwd=tmp_path, env=env,
                            capture_output=True, text=True, check=False)  # fmt: skip

    assert (result.returncode, result.stdout.strip(), result.stderr) == (0, "", "")


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completion_scripts_ask_fsbpyr_for_the_words(
    shell: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["completion", shell]) == 0
    assert f"_FSBPYR_COMPLETE={shell}_complete" in capsys.readouterr().out


def test_completion_refuses_other_shells(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["completion", "tcsh"]) == 2
    err = capsys.readouterr().err
    assert "Usage: fsbpyr completion [OPTIONS] SHELL" in err
    assert "'tcsh' is not one of 'bash', 'zsh', 'fish'" in err


def test_bash_completes_through_the_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real round trip: bash runs the script's function as complete -F does (command, word,
    previous word), and the function runs fsbpyr from PATH."""
    bash = shutil.which("bash")
    command = Path(sys.executable).with_name("fsbpyr")
    if bash is None or not command.is_file():
        pytest.skip("needs bash and the fsbpyr console script next to the interpreter")
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    main(["completion", "bash"])
    (tmp_path / "fsbpyr.bash").write_text(capsys.readouterr().out)
    session = ('source fsbpyr.bash; COMP_WORDS=(fsbpyr build --entry ""); COMP_CWORD=3; '
               '_fsbpyr_completion fsbpyr "" --entry; '
               'printf "%s\\n" "${COMPREPLY[@]}"')  # fmt: skip
    env = {**os.environ, "PATH": f"{command.parent}{os.pathsep}{os.environ['PATH']}"}

    result = subprocess.run([bash, "--norc", "-c", session], cwd=tmp_path, env=env,
                            capture_output=True, text=True, check=True)  # fmt: skip

    assert result.stdout.split() == ["mytool", "other"]


# --- man page --------------------------------------------------------------------------------


def test_man_page_documents_every_command_and_option(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["man"]) == 0
    page = capsys.readouterr().out

    assert page.startswith('.TH FSBPYR 1 "" "fsbpyr ')
    for ctx in _commands(click.Context(cli, info_name="fsbpyr")):
        assert f'.SS "{ctx.command_path}' in page
        for param in ctx.command.get_params(ctx):
            if isinstance(param, click.Option) and param.name != "help":
                for flag in (*param.opts, *param.secondary_opts):
                    assert flag.replace("-", "\\-") in page, flag


def test_man_page_ends_with_the_examples(capsys: pytest.CaptureFixture[str]) -> None:
    main(["man"])
    page = capsys.readouterr().out

    examples = page[page.index(".SH EXAMPLES") :]
    assert ".SH" not in examples.removeprefix(".SH EXAMPLES")
    for command, _ in EXAMPLES:
        assert f".TP\n\\fB{command.replace('-', '\\-')}\\fR\n" in examples


def test_example_command_lines_are_never_hyphenated(capsys: pytest.CaptureFixture[str]) -> None:
    groff = shutil.which("groff")
    if groff is None:
        pytest.skip("needs groff")
    main(["man"])

    plain = [groff, "-man", "-Tascii", "-P-cbou", "-rLL=80n"]  # no bold, colour or overstrike
    result = subprocess.run(plain, input=capsys.readouterr().out, capture_output=True, text=True,
                            check=True)  # fmt: skip

    examples = result.stdout.split("EXAMPLES")[1].splitlines()
    assert [line for line in examples if line.endswith("-")] == []


def test_every_example_is_a_command_line_fsbpyr_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parsed and checked by the commands themselves, which then do nothing."""
    for ctx in _commands(click.Context(cli, info_name="fsbpyr")):
        monkeypatch.setattr(ctx.command, "callback", lambda **_: 0)

    for command, _ in EXAMPLES:
        argv = shlex.split(command.partition(" > ")[0])
        assert argv[0] == "fsbpyr"
        assert main(argv[1:]) == 0, command


def test_man_page_sets_code_in_bold(capsys: pytest.CaptureFixture[str]) -> None:
    main(["man"])

    assert "\\fBmodule:function\\fR" in capsys.readouterr().out


def test_man_page_is_clean_roff(capsys: pytest.CaptureFixture[str]) -> None:
    groff = shutil.which("groff")
    if groff is None:
        pytest.skip("needs groff")
    main(["man"])

    result = subprocess.run([groff, "-man", "-Tutf8", "-ww", "-z"], input=capsys.readouterr().out,
                            capture_output=True, text=True, check=True)  # fmt: skip

    assert result.stderr == ""


# --- README ----------------------------------------------------------------------------------


def test_readme_option_table_is_the_build_commands_help() -> None:
    lines = (ROOT / "README.md").read_text().splitlines()
    start = lines.index("| Option | |") + 2
    end = lines.index("", start)

    assert lines[start:end] == _option_rows(cli.commands["build"])


def test_readme_supported_targets_name_the_pinned_cpython() -> None:
    readme = (ROOT / "README.md").read_text()
    section = readme.partition("\n## Supported targets\n")[2].partition("\n## ")[0]
    pin = pins.pin_for(TARGET_PYTHON, "x86_64-unknown-linux-gnu")

    assert f"CPython {pin.version}" in section


def test_readme_quickstart_runs_command_lines_fsbpyr_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parsed and checked by the commands themselves, which then do nothing; tests/e2e runs
    the whole quickstart."""
    for ctx in _commands(click.Context(cli, info_name="fsbpyr")):
        monkeypatch.setattr(ctx.command, "callback", lambda **_: 0)
    lines = [shlex.split(line, comments=True) for line in quickstart().splitlines()]
    commands = [argv[1:] for argv in lines if argv and Path(argv[0]).name == "fsbpyr"]

    assert [argv[0] for argv in commands] == ["build", "inspect"]
    for argv in commands:
        assert main(argv) == 0, argv


def test_readme_tool_fsbpyr_sample_is_a_table_builds_accept(tmp_path: Path) -> None:
    readme = (ROOT / "README.md").read_text()
    sample = readme.partition("```toml\n[tool.fsbpyr]\n")[2].partition("```")[0]
    (tmp_path / "pyproject.toml").write_text("[tool.fsbpyr]\n" + sample)

    config = load_config(Project.load(tmp_path))

    assert config.entry == "mytool"
