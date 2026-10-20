"""Build-time scan for files read relative to ``__file__`` (plan Phase 6b)."""

import pytest

from fsbpyr.scan import file_relative_existence_checks, file_relative_reads


@pytest.mark.parametrize(
    "source",
    [
        "import os\nopen(os.path.join(os.path.dirname(__file__), 'data.txt'))\n",
        "import os\nHERE = os.path.dirname(__file__)\nDATA = os.path.join(HERE, 'd')\n"
        "names = os.listdir(DATA)\n",
        "from pathlib import Path\ntext = Path(__file__).parent.joinpath('x').read_text()\n",
        "from pathlib import Path\nROOT = Path(__file__).parent\nf = (ROOT / 'x').open()\n",
        "import ctypes, os\nlib = ctypes.CDLL(os.path.join(os.path.dirname(__file__), 'l.so'))\n",
        "import glob, os\nfiles = glob.glob(os.path.dirname(__file__) + '/*.json')\n",
        "import os, pkg\nopen(os.path.dirname(pkg.__file__) + '/d')\n",
        "import os\nopen(os.path.join(__path__[0], 'd'))\n",
        "import os, subprocess\nsubprocess.run([os.path.dirname(__file__) + '/t'])\n",
        "import os\nos.execv(os.path.join(os.path.dirname(__file__), 'tool'), ['tool'])\n",
        "import cffi\nHERE = __file__\nlib = cffi.FFI().dlopen(HERE + '/l.so')\n",
        "import configparser, os\nc = configparser.ConfigParser()\n"
        "c.read(os.path.join(os.path.dirname(__file__), 'x.ini'))\n",
        "import numpy as np, os\nnp.load(os.path.join(os.path.dirname(__file__), 'x.npy'))\n",
        "import jinja2\nenv = jinja2.Environment(jinja2.PackageLoader('pkg', 'tpl'))\n",
    ],
    ids=["open", "through-names", "pathlib-read", "pathlib-open", "ctypes", "glob", "x-file",
         "path-0", "subprocess", "os-exec", "cffi-dlopen", "configparser", "np-load",
         "jinja2-package-loader"],
)
def test_reads_next_to_file_are_found(source: str) -> None:
    lines = file_relative_reads(source.encode(), "m.py")

    assert lines == [source.count("\n")]  # the last line holds the read


@pytest.mark.parametrize(
    "source",
    [
        "import os\nHERE = os.path.dirname(__file__)\nok = os.path.exists(HERE)\n",
        "print(__file__)\n",
        "import importlib.resources\ndata = importlib.resources.files(__name__)\n",
        "f = open('config.txt')\n",
        "import os\nHERE = os.path.dirname(__file__)\nwith open('x') as f:\n    f.read()\n",
        "def broken(:\n    open(__file__)\n",
        "if __name__ == '__main__':\n    open(__file__).read()\n",
    ],
    ids=[
        "exists-only",
        "no-read",
        "resources",
        "unrelated-open",
        "unrelated-method",
        "syntax",
        "main-guard",
    ],
)
def test_other_code_is_not_reported(source: str) -> None:
    assert file_relative_reads(source.encode(), "m.py") == []


@pytest.mark.parametrize(
    "source",
    [
        "import os\nHERE = os.path.dirname(__file__)\nok = os.path.exists(HERE)\n",
        "from pathlib import Path\nok = (Path(__file__).parent / 'x').is_file()\n",
    ],
    ids=["os-path", "pathlib"],
)
def test_existence_checks_are_a_separate_result(source: str) -> None:
    assert file_relative_existence_checks(source.encode(), "m.py") == [source.count("\n")]
    assert file_relative_reads(source.encode(), "m.py") == []
