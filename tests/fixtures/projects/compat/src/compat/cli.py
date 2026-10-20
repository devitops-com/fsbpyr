"""One small, real check per library of the compatibility matrix; each prints one line."""

import argparse


def _click(_: str | None) -> str:
    import click
    from click.testing import CliRunner

    @click.command()
    @click.option("--n", type=int)
    def command(n: int) -> None:
        click.echo(f"n={n}")

    return "click " + CliRunner().invoke(command, ["--n", "3"]).output.strip()


def _typer(_: str | None) -> str:
    import typer
    from typer.testing import CliRunner

    app = typer.Typer()

    @app.command()
    def hello(name: str) -> None:
        typer.echo(f"hello {name}")

    return "typer " + CliRunner().invoke(app, ["Ada"]).output.strip()


def _rich(_: str | None) -> str:
    import io

    from rich.cells import cell_len
    from rich.console import Console
    from rich.table import Table

    table = Table("key", "value")
    table.add_row("a", "1")
    console = Console(file=io.StringIO(), width=30, color_system=None)
    console.print(table)
    wide = cell_len("日本") == 4  # loads a width table module named unicode17-0-0
    return f"rich {'│ a   │ 1     │' in console.file.getvalue() and wide}"


def _requests(url: str | None) -> str:
    import requests

    response = requests.get(url or "", timeout=10)
    return f"requests {response.status_code} {response.text.strip()}"


def _charset_normalizer(_: str | None) -> str:
    from importlib.machinery import EXTENSION_SUFFIXES

    from charset_normalizer import md  # compiled with mypyc in the wheels

    return f"charset_normalizer {str(md.__file__).endswith(tuple(EXTENSION_SUFFIXES))}"


def _httpx(url: str | None) -> str:
    import httpx

    with httpx.Client(timeout=10) as client:  # verify=True: loads certifi's bundle
        response = client.get(url or "")
    return f"httpx {response.status_code} {response.text.strip()}"


def _certifi(_: str | None) -> str:
    import os
    import ssl

    import certifi

    path = certifi.where()
    context = ssl.create_default_context(cafile=path)
    return f"certifi {os.path.isfile(path)} {context.cert_store_stats()['x509_ca'] > 100}"


def _pydantic(_: str | None) -> str:
    from pydantic import BaseModel

    class Point(BaseModel):
        x: int
        y: int = 0

    return f"pydantic {Point.model_validate({'x': '1'}).model_dump()}"


def _jinja2(_: str | None) -> str:
    from jinja2 import Environment, PackageLoader

    environment = Environment(loader=PackageLoader("compat", "templates"))
    return "jinja2 " + environment.get_template("hello.txt").render(name="Ada").strip()


def _yaml(_: str | None) -> str:
    import yaml

    data = yaml.load("a: [1, 2]", Loader=yaml.CSafeLoader)
    return f"pyyaml {data} {yaml.__with_libyaml__}"


def _numpy(_: str | None) -> str:
    import numpy as np

    return f"numpy {np.arange(6).reshape(2, 3).sum(axis=0).tolist()}"


def _cryptography(_: str | None) -> str:
    from cryptography.fernet import Fernet

    fernet = Fernet(Fernet.generate_key())
    return f"cryptography {fernet.decrypt(fernet.encrypt(b'secret')).decode()}"


def _orjson(_: str | None) -> str:
    import orjson

    return f"orjson {orjson.dumps({'a': [1, 2]}).decode()}"


def _paths(_: str | None) -> str:
    import os
    from importlib.resources import as_file, files

    zoneinfo = files("tzdata") / "zoneinfo"
    with as_file(zoneinfo / "Europe") as europe:
        pass  # code written for installed packages may keep the path after the block
    kept = (europe / "Zagreb").read_bytes().startswith(b"TZif")
    with open(os.path.join(str(zoneinfo), "Asia", "Tokyo"), "rb") as handle:
        joined = handle.read(4) == b"TZif"  # str() of a resource directory, handed to open()
    return f"paths {kept} {joined}"


def _zoneinfo(_: str | None) -> str:
    import zoneinfo
    from datetime import datetime

    zoneinfo.reset_tzpath(to=[])  # no system database: the tzdata package must serve it
    offset = datetime(2026, 7, 1, tzinfo=zoneinfo.ZoneInfo("Europe/Zagreb")).utcoffset()
    return f"zoneinfo {offset} {len(zoneinfo.available_timezones()) > 400}"


def _sqlalchemy(_: str | None) -> str:
    from sqlalchemy import create_engine, text
    from sqlalchemy.util import has_compiled_ext

    with create_engine("sqlite://").begin() as connection:
        connection.execute(text("create table t (x int)"))
        connection.execute(text("insert into t values (1), (2)"))
        total = connection.execute(text("select sum(x) from t")).scalar()
    return f"sqlalchemy {total} {has_compiled_ext()}"


def _setuptools(_: str | None) -> str:
    import setuptools
    from distutils import core  # type: ignore[import-not-found]  # setuptools' own copy

    return f"setuptools {setuptools.__name__} {'_distutils' in core.__file__}"


CHECKS = {
    "click": _click, "typer": _typer, "rich": _rich, "requests": _requests, "httpx": _httpx,
    "certifi": _certifi, "pydantic": _pydantic, "jinja2": _jinja2, "pyyaml": _yaml,
    "numpy": _numpy, "cryptography": _cryptography, "orjson": _orjson, "zoneinfo": _zoneinfo,
    "sqlalchemy": _sqlalchemy, "paths": _paths, "setuptools": _setuptools,
    "charset_normalizer": _charset_normalizer,
}  # fmt: skip


def main() -> None:
    parser = argparse.ArgumentParser(prog="compat")
    parser.add_argument("check", choices=sorted(CHECKS))
    parser.add_argument("url", nargs="?", help="URL for the HTTP client checks")
    args = parser.parse_args()
    print(CHECKS[args.check](args.url))
