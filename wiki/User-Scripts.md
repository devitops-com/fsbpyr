# Scripts and tools

Not every Python program is a packaged project. fsbpyr also builds a project without a build
system, a directory of loose scripts, a single script, and the tools a requirements file names.
The result is the same kind of executable as for a project, with a command per script and a
link per command. This page shows each case with a real run of fsbpyr 0.27.0, then how to
deploy the result, and what scripts find next to the executable. What is common to every build
(options, locks, the Python version) is on [Building an app](User-Building.md).

## What runs where

For all four cases:

- **Nothing is written into the source tree** (apart from the output, `dist/` by default), and
  the build runs none of the scripts: it reads them (their source, their first line) to find the
  commands and their imports. The smoke test only checks that each script is there. (A
  `module:function` command, one that a project without a build system declares in
  `[project.scripts]` or a tool's console script, is the exception: the smoke test imports its
  module, as for any console script.)
- **Only Python files are packaged:** `*.py` files and, in a directory of scripts or as the one
  script built alone, extensionless files whose first line is a python or `uv run` shebang
  (`#!/usr/bin/env python3`, `#!/usr/bin/python3`, `#!/usr/bin/env python`,
  `#!/usr/bin/env -S uv run --script`). A `.env`, a key, a template or a data file is never
  packaged. For a project or a directory of scripts in a git work tree, the files are the
  tracked ones (`git ls-files`): an untracked Python file that is not ignored, such as a
  `secrets.py`, is left out, and the build warns, naming it. Without git the files come from a
  walk. Either way, hidden directories, virtual environments (a directory with a `pyvenv.cfg`),
  `__pycache__`, `build`, `dist`, `node_modules` and `site-packages` are skipped. A link to a
  `.py` file outside the directory fails the build; links to directories are not followed. The
  home directory and `/` are refused as directories to build.
- **A script runs as `python3 script.py` would:** as `__main__`, with `sys.argv` as given
  (`argv[0]` the full path of the start) and its own directory first on `sys.path`, so
  `import utils` finds the `utils.py` or `utils/` beside it. A sibling named like a standard
  library module (`calendar.py`) wins over the library, as it does for CPython.

## Projects without a build system

A project with a `[project]` table and no `[build-system]` is what `uv init --no-package` writes:
`main.py` and a `pyproject.toml` without `[build-system]` (`uv lock`, or the first `uv run`, adds
`uv.lock`), to which the project adds its helpers. Poetry's `package-mode = false` and PDM's
`distribution = false` projects with a `[project]` table are built the same way, and so is a
Poetry 1 project with `package-mode = false` and no `[project]` table (its name, version, scripts
and dependencies are read from `[tool.poetry]`). A uv workspace root keeps its normal build.

fsbpyr writes the project's wheel itself, with the project's Python files under a reserved
top-level directory `_fsbpyr_scripts/`, runs no build backend, and installs the dependencies
from the first lock source of a project's build (`--requirements`, `uv.lock`, `pylock.toml`,
`requirements.txt`, or a [Poetry, PDM or Pipenv lock](User-Building.md#locks-from-poetry-pdm-and-pipenv)),
else from `[project.dependencies]` unlocked. The commands are `[project.scripts]` if present
(`main:run` runs `_fsbpyr_scripts.main:run`; a target under `src/` must be written
`_fsbpyr_scripts.src.pkg.mod:main`, and the build says so when it finds `pkg.mod:main` there),
else the modules with an `if __name__ == "__main__":` guard (and a top-level `__main__.py`): one
is the project's command, several make an executable with a command per file (`main.py` and
`__main__.py` named after the project). Test files (`test_*.py`, `*_test.py`, `conftest.py`, and
files below a `test/` or `tests/` directory) are never commands.

The test fixture [`scripts`](../tests/fixtures/projects/scripts)
has a `pyproject.toml` with only a `[project]` table, a `uv.lock`, and:

```python
# main.py
import sys

import utils
from helpers import greeting


def main() -> None:
    print(greeting(utils.shout(" ".join(sys.argv[1:]) or "world")))
    print("name", __name__, "args", sys.argv[1:])


if __name__ == "__main__":
    main()
```

with `utils.py` and a `helpers/` package beside it:

```console
$ fsbpyr build --python 3.14
fsbpyr: staging scripts from uv.lock
fsbpyr: collecting 1 distribution
fsbpyr: smoke-testing .../scripts/dist/.scripts.m_6o8xgc.tmp
fsbpyr: built .../scripts/dist/scripts (27.8 MB, entry _fsbpyr_scripts.main, glibc >= 2.34)
fsbpyr: target needs: glibc >= 2.34
fsbpyr: to deploy, copy the file and link it by each command's name (DEST: a directory on the host):
fsbpyr:   install -m 755 .../scripts/dist/scripts "$DEST"/scripts
fsbpyr: or let the copy make the links (and x.py for each script x) on the host: FSBPYR_INSTALL_LINKS="$DEST" "$DEST"/scripts
fsbpyr: a crontab, unit or agent line such as `python3 $DEST/x.py` must become `$DEST/x` or `$DEST/x.py` run directly: the link is an executable, not a Python file
$ dist/scripts Ada
hello ADA!
name __main__ args ['Ada']
```

A `.py` file below a directory whose name is no Python name (`sub-dir/c.py`) cannot be imported
in such a project: it is not packaged, and the build warns. A file named like `deploy-prod.py`
is not packaged either (a directory of scripts handles such names, below).

## A directory of scripts

`cd ops-scripts && fsbpyr build` builds a directory with no project file: no `setup.py`, no
`setup.cfg` (a `setup.cfg` alone is refused), and no `pyproject.toml` or one that holds only
`[tool]` tables. It is the case of a git repository of `.py` files and executable scripts that are
copied to hosts with the wrong Python or none, a set of monitoring plugins, a cron fleet. The
result is one executable named after the directory, and a link per command. The name is
normalised as a project name is: a directory `My_Tools/` gives `dist/my-tools`.

- **Commands:** a file with an `if __name__ == "__main__":` guard at module level, or a python or
  `uv run` shebang. The command is the file's name without `.py`, hyphens kept (`check-disk.py`
  and an extensionless `check-disk` both give `check-disk`); `main.py` and `__main__.py` take the
  directory's name. `conftest.py`, `test_*.py`, `*_test.py` and files below a `tests/` or `test/`
  directory are packaged but never commands. Two commands with one name (`a.py` and `sub/a.py`,
  `tool` and `tool.py`) fail the build, naming both. One command makes `dist/<command>`; several
  make `dist/<directory name>` and the links.
- **Names that are no Python names** (`check-disk.py`, `10-backup`, `sub-dir/`, `2024-tools/`)
  work all the same. The build stores such a file or directory under a name with each character
  no Python name can hold replaced by `_`, and a `_` in front when it starts with a digit
  (`check_disk.py`, `_10_backup.py`, `sub_dir/`, `_2024_tools/`); an extensionless file with a
  Python name (`queue`) gets `_x_` in front (`_x_queue.py`), so that it cannot shadow the
  standard library for its siblings. A script's `__file__` and `sys.path[0]` show the stored
  name. Two files or directories that give one stored name fail the build, naming both.
- **Dependencies** come only from what the directory declares: the file `--requirements` names,
  else the first of `pylock.toml`, `requirements.txt` and `Pipfile.lock`; else the union of the
  commands' PEP 723 blocks (`# /// script`), resolved at build time and so unlocked, their
  `requires-python` entries intersected (a conflict fails the build, naming the files); else the
  venv the scripts run in ([below](#dependencies-from-the-venv-the-scripts-run-in)); else none.
- **Settings:** a `pyproject.toml` beside the scripts that holds only `[tool]` tables (no
  `[project]`, `[build-system]` or `[tool.poetry]`), with no `setup.py` or `setup.cfg` beside it,
  is their settings file: its `[tool.fsbpyr]` (`python`, `glibc`, `strict`, ...) is read as for any project. A `[tool.fsbpyr]`
  table in a command's PEP 723 block is ignored in a directory build, with a warning naming the
  file.

The directory below is the one fsbpyr's own end-to-end test
[`test_loose_directory.py`](../tests/e2e/test_loose_directory.py)
builds: four commands (one extensionless, one whose name starts with a digit), two helper
modules, one of them named like the standard library's `calendar`, and a requirements file that
names a local library `greet`:

```
ops-scripts/
├── 10-backup           #!/usr/bin/env python3, prints 'backup' and __name__
├── calendar.py         MARK = 'the local calendar'
├── check-disk.py       #!/usr/bin/env python3, imports calendar, greet and util
├── check_mem.py        imports util; a main guard
├── report.py           a main guard; prints its arguments
├── requirements.txt    ../greetlib
└── util.py             VALUE = 42
```

```console
$ fsbpyr build --python 3.14
fsbpyr: staging ops-scripts from requirements
fsbpyr: collecting 2 distributions
fsbpyr: smoke-testing .../ops-scripts/dist/.ops-scripts.gg8t7isk.tmp
fsbpyr: warning: requirements not pinned with ==, a commit SHA or a hash, so resolved now and not reproducible: ../greetlib
fsbpyr: built .../ops-scripts/dist/ops-scripts (27.8 MB, 4 commands, glibc >= 2.34)
fsbpyr: commands: 10-backup, check-disk, check_mem, report; start one through its link, or as ops-scripts COMMAND
fsbpyr: linked .../ops-scripts/dist/10-backup -> ops-scripts
fsbpyr: linked .../ops-scripts/dist/check-disk -> ops-scripts
fsbpyr: linked .../ops-scripts/dist/check_mem -> ops-scripts
fsbpyr: linked .../ops-scripts/dist/report -> ops-scripts
fsbpyr: target needs: glibc >= 2.34
fsbpyr: to deploy, copy the file and link it by each command's name (DEST: a directory on the host):
fsbpyr:   install -m 755 .../ops-scripts/dist/ops-scripts "$DEST"/ops-scripts
fsbpyr:   for c in 10-backup check-disk check_mem report; do ln -sf ops-scripts "$DEST/$c"; done
fsbpyr: or let the copy make the links (and x.py for each script x) on the host: FSBPYR_INSTALL_LINKS="$DEST" "$DEST"/ops-scripts
fsbpyr: a crontab, unit or agent line such as `python3 $DEST/x.py` must become `$DEST/x` or `$DEST/x.py` run directly: the link is an executable, not a Python file
$ dist/check-disk -w 5
check-disk 42 hello from greet the local calendar ['-w', '5'] __main__
$ dist/10-backup
backup __main__
$ dist/ops-scripts report a --b
report ['a', '--b']
$ fsbpyr inspect dist/ops-scripts
app        ops-scripts 0
...
lock       requirements.txt sha256:426dfedf43d1f6828e03123074e0ad73f64325ec884562721689fbd637c07b9d
...
commands   10-backup = _fsbpyr_scripts._10_backup
           check-disk = _fsbpyr_scripts.check_disk
           check_mem = _fsbpyr_scripts.check_mem
           report = _fsbpyr_scripts.report
```

(The warning is about the unpinned local path; a real directory would pin its requirements.)

### Imports are checked

The build follows each command's imports, statically, through the scripts and the modules they
reach. A required import that is neither a file found from the script's own directory (or from
a directory it adds to `sys.path`), an installed distribution nor in the standard library
**skips that command** with a warning naming `file:line`; the other commands are still built.
`--strict` fails the build, and so does a build with no command left (`no command can run in
the executable, each imports what it does not hold`). A standard library module that the
build's Python removed is named as such, with the newest supported `--python` that still has it.
An import under `try`/`except ImportError` (or `ModuleNotFoundError`, `Exception`,
`BaseException`, a bare `except`), in that `except` branch, under `if TYPE_CHECKING:` or under a
test of `sys.platform`, `os.name` or `sys.version_info` is optional. A directory that a script
adds to `sys.path` with a path built from its own `__file__`
(`sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pylib"))`) is
searched, `..` included; a `sys.path` change the check cannot follow makes the command's
unresolved imports optional.

A directory `old-ops` with four commands and nothing declared: `hello.py`, a `conf.py` that
reads `settings.json` next to its `__file__` (used [below](#files-next-to-a-script)), a
`fetch.py` that imports `requests`, and an `old.py` that imports `cgi` (removed in Python 3.13):

```console
$ fsbpyr build --python 3.14
fsbpyr: staging old-ops from unlocked
...
fsbpyr: warning: command fetch skipped: not in the executable: fetch.py:1: import requests. declare it in requirements.txt, or in a '# /// script' block of the script (a project's own file is found next to the script that imports it)
fsbpyr: warning: command old skipped: not in the executable: old.py:1: import cgi. cgi was removed in Python 3.13; build with --python 3.12
fsbpyr: built .../old-ops/dist/old-ops (27.8 MB, 2 commands, glibc >= 2.34)
...
$ fsbpyr build --python 3.14 --strict -o dist/strict
fsbpyr: staging old-ops from unlocked
fsbpyr: error: --strict: the build has 2 warnings; nothing was written:
  command fetch skipped: not in the executable: fetch.py:1: import requests. ...
  command old skipped: not in the executable: old.py:1: import cgi. cgi was removed in Python 3.13; build with --python 3.12
```

With a `.venv` directory there, the warning's last hint is instead
`uv pip freeze --python .venv/bin/python > requirements.txt`, which writes what the venv holds
for the build to read (for a single script, without `> requirements.txt`: it lists what to put
in the block). Python 2 is not bundled: an extensionless file with a `python2` shebang is no
script and is not packaged, a command that does not parse as Python 3 is skipped with a warning
(`file:line: cannot be parsed: ...`), and a file that does not compile is kept as data, with a
warning.

### Dependencies from the venv the scripts run in

When nothing declares a dependency (no requirements file, `pylock.toml`, lock or PEP 723
`dependencies`) and a command's import is unresolved, the build reads the venv the scripts run
in: `.venv`, `venv` or `env` in the directory (one with a `pyvenv.cfg`), else `$VIRTUAL_ENV`. It
reads files only and never starts the venv's Python: each installed distribution's `METADATA`,
`top_level.txt` and `RECORD` say which one provides the import (a package name is never guessed
from an import name). Those distributions, and what their `Requires-Dist` need as installed, are
pinned `name==version` and installed fresh by uv for the target; nothing is copied from the venv.
The build warns that the dependencies came from the venv and prints the
`uv pip freeze --python <venv>/bin/python > requirements.txt` that declares them (for a single
script, without `> requirements.txt`, for the block's `dependencies`); `--strict` fails on that
warning. A pin that uv cannot install fails the build, with uv's reason; an editable or path
install that is needed is refused. `fsbpyr inspect` shows the lock as `inferred from <venv>`
with the pins.

## A single script

`fsbpyr build report.py` builds one script: a `*.py` file, or an extensionless file whose first
line is a python or `uv run --script` shebang. The app is named after the file's stem, hyphens
kept, and written to `dist/<name>` in the script's directory, or to `-o`.

- **What is packaged:** the script and its static local-import closure: the modules and packages
  it imports from its own directory, transitively, and a directory it adds to `sys.path` from its
  `__file__`. The rest of the directory is not read, so a script among 200 unrelated ones carries
  its own few modules. A `pyproject.toml`, `requirements.txt`, `pylock.toml` or `uv.lock` beside
  the script is not read; `--requirements FILE` is. A `.python-version` beside it is read, and
  chooses the Python as for a project ([Which Python](User-Building.md#which-python)).
- **Dependencies, Python and settings** come from the script's PEP 723 block: `dependencies`
  are resolved at build time (the build warns that it is unlocked), `requires-python` chooses
  the Python as a project's does, and a `[tool.fsbpyr]` table in the block (`# [tool.fsbpyr]`,
  `# strict = true`) is read as in `pyproject.toml`, unknown keys rejected. When no block (or a
  block without `dependencies`) declares a dependency, a venv beside the script can supply pins,
  as for a directory. Without either, a script that imports a package fails to build, since its
  one command is skipped:

  ```console
  $ fsbpyr build --python 3.14 fetch.py
  fsbpyr: staging fetch from unlocked
  fsbpyr: warning: command fetch skipped: not in the executable: fetch.py:1: import requests. declare it in the dependencies of the script's '# /// script' block (a project's own file is found next to the script that imports it)
  fsbpyr: error: fetch: no command can run in the executable, each imports what it does not hold
  $ echo $?
  1
  ```
- **A script lock:** a `report.py.lock` beside the script (written by `uv lock --script
  report.py`) locks the build. A block that has outgrown the lock fails the build (run
  `uv lock --script` again). `fsbpyr inspect` shows the lock as `report.py.lock sha256:...`
  (its kind in `inspect --json` is `script.lock`). Apart from its output (`dist/` by default),
  fsbpyr writes nothing beside the script: no lock, environment or cache.

The case of the end-to-end test
[`test_single_script.py`](../tests/e2e/test_single_script.py):
`report.py` with a block and a sibling `helpers.py`, and an unrelated `other.py` beside them:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
import sys

import helpers

if __name__ == '__main__':
    print('report', helpers.VALUE, sys.argv[1:], __name__)
```

```console
$ fsbpyr build --python 3.14 ops/report.py
fsbpyr: staging report from unlocked
fsbpyr: collecting 1 distribution
fsbpyr: smoke-testing .../ops/dist/.report.9nu5symu.tmp
fsbpyr: built .../ops/dist/report (27.8 MB, entry _fsbpyr_scripts.report, glibc >= 2.34)
...
$ ops/dist/report -x --y
report 42 ['-x', '--y'] __main__
$ fsbpyr inspect --all-modules ops/dist/report
...
  _fsbpyr_scripts  _fsbpyr_scripts.helpers  _fsbpyr_scripts.report
```

`other.py` is not in the executable.

## A tool from its requirements file

`cd tools && fsbpyr build` in a directory with **no Python file** and a lock source builds the
tools the lock names: a `requirements.txt` saying `httpie==3.2.4`, a `pylock.toml`, a
`Pipfile.lock`, or a file given with `--requirements` (or `requirements` in a settings-only
`pyproject.toml`). The result is one executable named after the directory (`<directory>-tools`
when a requirement has the directory's name), whose commands are the console and gui scripts of
those tools, with a link per command and the same deploy lines as a directory of scripts; a
single command makes `dist/<command>`. A directory that holds a Python file is a directory of
scripts instead, whatever its requirements say.

- **Which tools:** the distributions the file names directly (with those of the files it
  includes with `-r`). A file that lists every installed distribution, as `pip freeze`,
  `uv pip freeze` and `pylock.toml` do, names the dependencies too, so there the tools are the
  listed distributions that no other listed one requires. A `Pipfile.lock` with a `Pipfile`
  beside it takes the Pipfile's `[packages]`.
- **Commands:** the tools' own `console_scripts` and `gui_scripts`; a dependency's scripts are
  not commands. A tool with no script of its own falls through to its direct requirements when
  it is a meta-package (it installs no code, as `jupyter`), and else only to its requirements
  named `<tool>-...`; a distribution without a script that gives no tool that way is a library,
  and the build says so. A tool also gets the commands of its family, its requirements named
  `<tool>-...` (`ansible` gives `ansible-core`'s `ansible-playbook` and the others). Two tools
  with one script name fail the build, naming both. `--entry NAME` builds one of the commands
  alone.
- **Build:** the lock is installed as for any pip project; nothing of the directory is packaged.

The case of the end-to-end test
[`test_tool_binary.py`](../tests/e2e/test_tool_binary.py),
a `linters/requirements.txt` written by `pip freeze` of an environment with flake8:

```
flake8==7.1.1
mccabe==0.7.0
pycodestyle==2.12.1
pyflakes==3.2.0
```

```console
$ cd linters && fsbpyr build --python 3.14
fsbpyr: staging linters from requirements
fsbpyr: requirements.txt lists all 4 installed distributions, so the tools are the 1 that no other listed distribution requires: flake8
fsbpyr: commands: flake8 (flake8 7.1.1)
fsbpyr: collecting 5 distributions
fsbpyr: smoke-testing .../linters/dist/.flake8.5tle0jxy.tmp
fsbpyr: built .../linters/dist/flake8 (28.2 MB, entry flake8.main.cli:main, glibc >= 2.34)
...
$ cd .. && linters/dist/flake8 sample.py
sample.py:1:1: F401 'os' imported but unused
sample.py:4:7: E201 whitespace after '('
sample.py:4:9: E202 whitespace before ')'
$ linters/dist/flake8 --version
7.1.1 (mccabe: 0.7.0, pycodestyle: 2.12.1, pyflakes: 3.2.0) CPython 3.14.9 on
Linux
```

Tools differ in what they need:

- **Data files:** real tools may need [`fs-packages`](User-Settings.md#fs-packages). A
  `requirements.txt` of `ansible-core==2.18.6` builds into an executable of its ten commands,
  but only with `--fs-package ansible`; without it the smoke test fails with
  `ERROR: Missing base YAML definition file (bad install?): .../ansible/config/base.yml`.
- **Helper interpreters:** a helper that a tool starts through `sys.executable` (`-m`, `-c`, a
  script of the executable) is refused with exit 70 unless the tool is built with
  `--interpreter-reexec`; the build prints its usual note when it packages a library known to
  do so. A tool that runs a file of the host through `sys.executable` gets the executable's
  `unknown command` instead. In that `ansible-core` build, `ansible localhost -m ping -c local`
  fails with `tools: unknown command '.../AnsiballZ_ping.py'` in its module output, and
  succeeds given a real interpreter: `-e ansible_python_interpreter=/usr/bin/python3`.
- **Interpreter-like commands** (`ipython`, `ansible-console`) are reachable through the
  executable's own name too (`tools ipython`), so a `sudo` rule must name the command's link,
  not the executable.
- **Licences:** you redistribute the tools; `fsbpyr inspect --licenses` lists the notices they
  ship, as for any app.

## Deploying

Each command is a link to the executable, so a host needs the executable and a link per command.
The build prints the lines to do it. Either copy and link by hand:

```sh
install -m 755 dist/ops-scripts "$DEST"/ops-scripts
for c in 10-backup check-disk check_mem report; do ln -sf ops-scripts "$DEST/$c"; done
```

or let the copy on the host make its own links, with `FSBPYR_INSTALL_LINKS=DIR`. It links each
command into `DIR`, as an absolute symlink to the executable's real path, and `x.py` beside `x`
for each script, prints what it did, and exits without running the app:

```console
$ FSBPYR_INSTALL_LINKS=$PWD/../bin dist/ops-scripts
fsbpyr: linked .../bin/10-backup -> .../ops-scripts/dist/ops-scripts
fsbpyr: linked .../bin/10-backup.py -> .../ops-scripts/dist/ops-scripts
fsbpyr: linked .../bin/check-disk -> .../ops-scripts/dist/ops-scripts
fsbpyr: linked .../bin/check-disk.py -> .../ops-scripts/dist/ops-scripts
...
$ FSBPYR_INSTALL_LINKS=$PWD/../bin dist/ops-scripts
fsbpyr: present .../bin/10-backup -> .../ops-scripts/dist/ops-scripts
...
$ ../bin/check-disk.py x
check-disk 42 hello from greet the local calendar ['x'] __main__
```

A second run changes nothing. A file, a directory or a link to something else in the way is
reported and kept, the other links are still made, and the exit status is 1; a `DIR` that is
missing or not writable is an error before any link. There is no `--install` option on purpose:
under a sudo rule such as `ops ALL=(root) /opt/tools/ops-scripts *` it would let a caller create
root-owned links anywhere. sudo's default `env_reset` drops `FSBPYR_*`, so
`sudo FSBPYR_INSTALL_LINKS=DIR` works only where `env_keep` or `SETENV` allows it.

A crontab, unit or agent line that ran `python3 /opt/tools/check-disk.py` must become
`/opt/tools/check-disk` (or `/opt/tools/check-disk.py`, run directly): the link is an executable,
not a Python file.

### Monitoring plugins and cron fleets

Nagios, Icinga, NRPE and Checkmk checks and cron jobs call a script by the name a command
definition holds, many times an hour. Give each link that name. Keep the executable out of
directories that an agent runs file by file (`cron.daily`, a monitoring agent's directory of
local checks), since it would run the executable itself as well: put it in a directory of its
own and link the commands where the agent looks, with an absolute target
(`ln -sf /opt/tools/ops-scripts /etc/cron.daily/check-disk`).

- All links of an executable share **one instance limit** for each user: 4 per CPU the process
  may run on, plus 16, at least 64 and at most 1024. A start beyond it exits 70. `FSBPYR_MAX_INSTANCES` sets it (at most 4096; `0` turns the limit off) for
  a host that starts many checks at once.
- Every start checks the whole executable's digest ([Running](User-Running.md#integrity)); the
  cost grows with the file's size, and one executable serves every command.

## Files next to a script

A script's `__file__` is where the script would be beside the executable: the executable's
directory plus the file's path in the project (`/opt/tools/sub/x.py` for `sub/x.py`, with the
executable in `/opt/tools`). So `open(os.path.join(os.path.dirname(__file__), "config.ini"))`
reads the `config.ini` deployed **beside the executable**, as a script run by path reads the one
beside it. The executable holds the project's Python files only: put config, state and data
files next to it. A file or directory that is no Python name is deployed under its stored name
(`sub_dir/`).

The code is still the executable's: a stale `check-disk.py` left beside the binary by an earlier
copy is never imported, and `sys.path.append(os.path.dirname(__file__) + "/pylib")` gets the
packaged `pylib`. A traceback shows the packaged source lines. The script's own source is no file
there (`open(__file__)` fails unless a copy is deployed); the loader's `get_source` gives it. A build with `--no-source` keeps no sources, so it has neither the source
lines nor the source.

The build warns for each such read it finds outside the script's
`if __name__ == "__main__":` block (`conf.py:4: reads a file next to its own __file__, which is
read next to the executable ...: deploy the file there, beside the binary`); it is a heuristic
that knows `open`, `read_text`, `os.listdir` and the like. At run time, an uncaught
`FileNotFoundError` there says where the file was expected:

```console
$ dist/conf
Traceback (most recent call last):
  ...
FileNotFoundError: [Errno 2] No such file or directory: '.../old-ops/dist/settings.json'
fsbpyr: .../old-ops/dist/settings.json is missing; the files a script reads next to its __file__ are read next to the executable, from .../old-ops/dist/: deploy it there
$ cp settings.json dist/ && dist/conf
{'a': 1}
```

A package of a project with a build system is different: its `__file__` is below the
executable, and [`fs-packages`](User-Settings.md#fs-packages) applies.
