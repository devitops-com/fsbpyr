# Worked examples

fsbpyr is tested on a corpus of 65 small applications in
[`tests/corpus`](../tests/corpus). Each one uses
real libraries the way real code does. The corpus test builds each app, runs its commands both
in a virtual environment and as the built executable, and requires the same exit code, output
and errors. This page shows how to try a corpus app yourself, walks through six of them, maps every
app to the use case it stands for, and lists the examples of scripts, several commands and
other locks that the other pages take from fsbpyr's test fixtures and end-to-end tests.

All outputs on this page and the other User Guide pages come from fsbpyr 0.27.0 (the released
`fsbpyr-3.14-x86_64-linux-gnu`) with uv 0.13.6 on x86-64 Linux, on 2026-10-27. That uv is not
the 0.13.5 the release was built with, so every build also printed the warning that says so;
the outputs leave it out ([Building](User-Building.md#a-first-build) shows it). Paths are
shortened to `...`.

## Trying a corpus app

Each app is a complete project: `pyproject.toml`, `uv.lock`, `src/`, and `commands.toml` (the
commands the test runs). Copy one out of a checkout and build it:

```sh
git clone --depth 1 --branch v0.27.0 https://github.com/devitops-com/fsbpyr.git
cp -r fsbpyr/tests/corpus/pandas-csv . && cd pandas-csv
fsbpyr build --python 3.14
cat commands.toml        # what to run: here argv = ["work/sales.csv"]
dist/corpus-pandas-csv work/sales.csv
```

> **Run apps you build from the corpus with care.** The corpus test runs every command under a
> process limit. A few apps start worker processes; if you change their settings, run them
> under a limit too, for example
> `systemd-run --user --scope -p TasksMax=256 -p MemoryMax=8G -- dist/<app>`.

## 1. A data tool with no settings: `pandas-csv`

[`tests/corpus/pandas-csv`](../tests/corpus/pandas-csv)
reads a CSV file and sums it by group:

```python
def main() -> None:
    frame = pd.read_csv(sys.argv[1], parse_dates=["day"])
    totals = frame.groupby("region", sort=True)["amount"].agg(["sum", "mean", "count"])
    print(totals.round(2).to_string())
    print(frame["day"].dt.month.value_counts().sort_index().to_string())
```

It needs no `[tool.fsbpyr]` table. The build and its output are on
[Building an app](User-Building.md#a-first-build), and its `inspect` output on
[Inspecting](User-Inspecting.md#the-summary). It is also the example for the `--strict`,
`--no-source` (65.1 MB → 59.2 MB) and `--smoke-args` options, and for `FSBPYR_TRACE`.

## 2. A web app with templates: `flask-jinja`

[`tests/corpus/flask-jinja`](../tests/corpus/flask-jinja)
renders a Jinja template and serves a static file, both kept next to its package. Without
`fs-packages = ["corpus_flask"]` the build succeeds but the app answers 500 with
`TemplateNotFound`; with it, it answers `<p>Hello Ada!</p>`. The full before and after is on
[Settings: fs-packages](User-Settings.md#fs-packages).

## 3. Machine learning with worker processes: `sklearn`

[`tests/corpus/sklearn`](../tests/corpus/sklearn)
loads scikit-learn's bundled datasets and runs cross-validation and a random forest with
`n_jobs=2`. It needs two settings, and its `pyproject.toml` says why:

```toml
[tool.fsbpyr]
# Without it the build's smoke test fails: sklearn/utils/_repr_html/estimator.py reads
# estimator.css next to its __file__ (NotADirectoryError below the executable)
fs-packages = ["sklearn"]
# Without it joblib's loky workers (`sys.executable -m joblib...popen_loky_posix`) and its
# resource tracker (`sys.executable -c ...`) are refused with exit 70, and n_jobs=2 fails with
# TerminatedWorkerError.
interpreter-reexec = true
```

What happens without `interpreter-reexec` is on
[Settings: interpreter-reexec](User-Settings.md#interpreter-reexec).

## 4. A library the target must provide: `boosting`

[`tests/corpus/boosting`](../tests/corpus/boosting)
trains xgboost and lightgbm models. lightgbm's library needs GNU OpenMP (`libgomp.so.1`), which
its wheel does not bundle, so the build stops until the project allows it, and both packages
load their libraries with ctypes from paths built from `__file__`:

```toml
[tool.fsbpyr]
allow-system-libs = ["libgomp.so.1"]
fs-packages = ["lightgbm", "sklearn", "xgboost"]
```

```console
$ dist/corpus-boosting
data (300, 8) 150
xgboost accuracy 0.97
xgboost predict [0.2078, 0.0671, 0.0562]
lightgbm accuracy 0.9367
lightgbm predict [0.3522, 0.1887, 0.44]
saved ['model.json', 'model.txt']
$ FSBPYR_SELFTEST=natives dist/corpus-boosting
fsbpyr selftest: all natives load (196 extensions)
```

The build error without the setting is on
[Settings: allow-system-libs](User-Settings.md#allow-system-libs). This app is 449.5 MB: xgboost
depends on `nvidia-nccl-cu13`, NVIDIA's NCCL library, and the build warns that NCCL's Python code
names a library that no extension module loads.

## 5. A web service with no settings: `fastapi`

[`tests/corpus/fastapi`](../tests/corpus/fastapi)
runs FastAPI with pydantic through its test client, then serves it with uvicorn (uvloop,
httptools, websockets) on 127.0.0.1 and talks to it over TCP. No settings:

```console
$ fsbpyr build --python 3.14
...
fsbpyr: built .../fastapi/dist/corpus-fastapi (40.0 MB, entry corpus_fastapi.cli:main, glibc >= 2.34)
fsbpyr: target needs: glibc >= 2.34; libgcc_s.so.1 (GCC_4.2.0)
$ dist/corpus-fastapi 2>/dev/null
hello {'message': 'hello Ada'}
create 201 {'name': 'pen', 'price': 1.5, 'tags': ['a']}
invalid 422
  error ['body', 'name'] string_too_short
  error ['body', 'price'] greater_than
background ['pen']
websocket echo in-process
openapi 3.1.0 ['/hello/{who}', '/items']
schema Item ['name', 'price', 'tags']
uvicorn http 200 {'message': 'hello Grace'}
uvicorn websocket echo over tcp
uvicorn stopped True
```

(Two deprecation warnings, from starlette and uvicorn, go to stderr.)

## 6. A developer tool compiled with mypyc: `black`

[`tests/corpus/black`](../tests/corpus/black)
runs black itself. black ships modules compiled with mypyc, which are native extensions, and its
`blib2to3` reads `Grammar.txt` next to its `__file__`. Without a setting the build's smoke test
fails:

```console
$ fsbpyr build --python 3.14 -o dist/no-setting
...
fsbpyr: smoke-testing .../black/dist/.no-setting.w1t9bwe3.tmp
fsbpyr: error: smoke test of the new .../black/dist/no-setting failed (exit 1):
Traceback (most recent call last):
  ...
  File "src/blib2to3/pgen2/pgen.py", line 27, in __init__
FileNotFoundError: [Errno 2] No such file or directory: '~/.cache/corpus-black/runtime/b4b966c88bfa50abd0c3502368deddcd/site/blib2to3/Grammar.txt'
fsbpyr: ~/.cache/corpus-black/runtime/b4b966c88bfa50abd0c3502368deddcd/site/blib2to3/Grammar.txt is missing from the native cache, which holds the compiled files of a package only; blib2to3 reads files next to its __file__: rebuild with --fs-package blib2to3
```

The compiled modules of black and blib2to3 are extracted into the app's cache, so their
`__file__` points there, and only the compiled files are extracted. fsbpyr's note names the
package and the option, `--fs-package blib2to3`; the corpus app sets it as
`fs-packages = ["blib2to3"]` in `[tool.fsbpyr]`. With it:

```console
$ dist/corpus-black --version
corpus-black, 26.10.1 (compiled: yes)
Python (CPython) 3.14.9
$ printf 'def f( a,b ):\n  return {"a":a}\n' | dist/corpus-black -q -
def f(a, b):
    return {"a": a}
```

## Every app, by use case

The compatibility notes group the corpus apps by field. Each group stands for a use case; the
settings each app needs, and the risk it covers, are on
[Compatibility](Dev-Compatibility.md#tested-on-real-applications).

| Use case | Apps | Apps with no settings |
|---|---|---|
| Machine-learning training and inference, data science notebooks | `torch`, `torchvision`, `lightning`, `transformers`, `hf-datasets`, `jax`, `keras`, `tensorflow`, `sklearn`, `boosting`, `onnx`, `faiss`, `numba`, `nlp`, `vision`, `matplotlib`, `jupyter`, `hydra`, `optuna` | 8 of 19 |
| Numerical and data tools: optimisation, statistics, dataframes, scientific file formats, geodata | `scipy`, `sympy`, `networkx`, `cvxpy`, `pulp`, `ortools`, `pyomo`, `casadi`, `statsmodels`, `hdf5`, `xarray`, `geo`, `dataframes`, `pandas-csv` | 7 of 14 |
| Physics and chemistry simulations | `astropy`, `pint`, `fitting`, `qutip`, `hep`, `materials`, `pyscf`, `qiskit`, `openmm` | 3 of 9 |
| Web services, workers and network clients | `fastapi`, `django`, `flask-jinja`, `aiohttp`, `gunicorn`, `grpc`, `celery`, `datastores`, `alembic`, `psycopg`, `messaging`, `auth`, `cloud`, `observability`, `gevent`, `twisted`, `validation` | 12 of 17 |
| Developer tools and libraries with native parts | `black`, `gnureadline`, `lxml`, `pillow`, `pytest-plugin`, `setuptools-vendor` | 5 of 6 |

On Python 3.15 (and, for `tensorflow`, on 3.14), apps whose lock needs a package without a
usable wheel for that Python are skipped ([Building: Python 3.15](User-Building.md#which-python)).

## Examples beyond the corpus

Every corpus app is a packaged project with one console script. The other ways fsbpyr builds a
program are shown, with real runs, on the pages that describe them. Each example is one of
fsbpyr's test fixtures, or the case of one of its end-to-end tests:

| Example | Source in the repository | Shown on |
|---|---|---|
| Two console scripts, one executable with a link per command | [`tests/fixtures/projects/multi`](../tests/fixtures/projects/multi) | [Building](User-Building.md#several-commands-in-one-executable) |
| A Poetry 2 project with `package-mode = false` and its `poetry.lock` | [`tests/fixtures/projects/foreign/poetry2`](../tests/fixtures/projects/foreign/poetry2) | [Building](User-Building.md#locks-from-poetry-pdm-and-pipenv) |
| A script with a `Pipfile` and `Pipfile.lock` | [`tests/fixtures/projects/foreign/pipenv`](../tests/fixtures/projects/foreign/pipenv) | [Building](User-Building.md#locks-from-poetry-pdm-and-pipenv) |
| A `uv init --no-package` project: `main.py`, `utils.py`, `helpers/` | [`tests/fixtures/projects/scripts`](../tests/fixtures/projects/scripts) | [Scripts and tools](User-Scripts.md#projects-without-a-build-system) |
| A directory of four scripts, a local library, `FSBPYR_INSTALL_LINKS` | [`tests/e2e/test_loose_directory.py`](../tests/e2e/test_loose_directory.py) | [Scripts and tools](User-Scripts.md#a-directory-of-scripts) |
| One script with a PEP 723 block and a sibling module | [`tests/e2e/test_single_script.py`](../tests/e2e/test_single_script.py) | [Scripts and tools](User-Scripts.md#a-single-script) |
| pandas-csv and `hello` built for EL8 (glibc 2.28), run in `rockylinux:8` and `almalinux:8` | [`tests/corpus/pandas-csv`](../tests/corpus/pandas-csv) | [Building for EL8](User-Building.md#building-for-el8-glibc-228) |
| flake8 built from a `pip freeze` | [`tests/e2e/test_tool_binary.py`](../tests/e2e/test_tool_binary.py) | [Scripts and tools](User-Scripts.md#a-tool-from-its-requirements-file) |
