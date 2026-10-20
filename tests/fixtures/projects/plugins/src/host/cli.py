"""Finds the plugins of the group `plugins.hooks` and the namespace package they share."""

import importlib
import importlib.metadata
import sys


def main() -> None:
    hooks = sorted(importlib.metadata.entry_points(group="plugins.hooks"), key=lambda e: e.name)
    for entry in hooks:
        print(entry.name, entry.value, entry.dist.name if entry.dist else None, entry.load()("x"))
    namespace = importlib.import_module("plugsns")
    print("namespace", getattr(namespace, "__file__", None), len(list(namespace.__path__)))
    print("distributions", sorted(d.name for d in importlib.metadata.distributions()
                                  if d.name.startswith("plugin")))
    print("files", sorted(str(f) for f in importlib.metadata.files("plugin-beta") or []
                          if f.suffix == ".py"))
    sys.stdout.flush()
