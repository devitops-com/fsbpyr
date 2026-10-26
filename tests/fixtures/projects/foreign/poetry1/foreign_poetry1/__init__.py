"""Prints the version of each distribution the lock may install, or "absent"."""

from importlib.metadata import PackageNotFoundError, version

import click
import rich  # noqa: F401  # the lock's rich brings markdown-it-py, mdurl and pygments


def installed(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "absent"


@click.command()
def main() -> None:
    for name in ("click", "rich", "markdown-it-py", "mdurl", "pygments", "colorama",
                 "packaging", "iniconfig"):  # fmt: skip
        click.echo(f"{name} {installed(name)}")
