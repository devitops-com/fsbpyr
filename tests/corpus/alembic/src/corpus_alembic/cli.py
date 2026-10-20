"""Upgrades an in-memory SQLite database through migrations whose file names start with digits."""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect


def main() -> None:
    config = Config()
    config.set_main_option("script_location", str(Path("work/migrations").resolve()))
    scripts = ScriptDirectory.from_config(config)
    if sys.argv[1:] == ["history"]:
        for script in scripts.walk_revisions():
            print(script.revision, script.doc)
        return
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        print("version", MigrationContext.configure(connection).get_current_revision())
        tables = inspect(connection).get_table_names()
        print("tables", sorted(tables))
