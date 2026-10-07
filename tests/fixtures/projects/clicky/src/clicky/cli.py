"""A small click application using rich and package data."""

from importlib.metadata import version
from importlib.resources import files

import click
from rich.console import Console


@click.command()
@click.argument("name", default="world")
@click.option("--shout", is_flag=True, help="Upper-case the greeting.")
def main(name: str, shout: bool) -> None:
    greeting = files("clicky.data").joinpath("greeting.txt").read_text().strip()
    text = f"{greeting}, {name}!"
    Console(color_system=None, width=80).print(text.upper() if shout else text)
    click.echo(f"click {version('click')}")
