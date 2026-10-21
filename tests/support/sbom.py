"""tools/release/sbom.py as a module: tools/ is no package, the release workflow runs the file."""

import importlib.util
from pathlib import Path
from types import ModuleType

PATH = Path(__file__).parents[2] / "tools" / "release" / "sbom.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fsbpyr_release_sbom", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
