"""A notebook run by nbclient in an ipykernel subprocess, IPython in-process, and jedi."""

import os
import tempfile


def notebook() -> None:
    import nbformat
    from nbclient import NotebookClient

    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_code_cell("6 * 7"),
        nbformat.v4.new_code_cell("print('hello from the kernel')"),
        nbformat.v4.new_code_cell("1 / 0"),
    ]
    nbformat.validate(nb)
    NotebookClient(nb, kernel_name="python3", timeout=60, allow_errors=True).execute()
    for index, cell in enumerate(nb.cells):
        for output in cell.outputs:
            kind = output.output_type
            if kind == "execute_result":
                print("cell", index, kind, output.data["text/plain"])
            elif kind == "stream":
                print("cell", index, kind, output.name, output.text.strip())
            elif kind == "error":
                print("cell", index, kind, output.ename, output.evalue)


def ipython() -> None:
    from IPython.core.interactiveshell import InteractiveShell
    from traitlets.config import Config

    config = Config()
    # its atexit hook would write below the deleted directory
    config.HistoryManager.hist_file = ":memory:"
    shell = InteractiveShell.instance(config=config)
    result = shell.run_cell("x = 2 ** 10\nx")
    print("ipython", result.success, result.result, shell.user_ns["x"])


def completions() -> None:
    import jedi

    names = [item.name for item in jedi.Script("import json\njson.du").complete()]
    print("jedi", sorted(names))


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        for name in ("JUPYTER_RUNTIME_DIR", "JUPYTER_DATA_DIR", "JUPYTER_CONFIG_DIR", "IPYTHONDIR"):
            os.environ[name] = os.path.join(directory, name.lower())
        os.environ["JUPYTER_PLATFORM_DIRS"] = "1"
        # The system's kernelspecs (/usr/share/jupyter/kernels: a host with python3-ipykernel
        # has one) come before ipykernel's own, which names sys.executable. The venv finds its
        # own spec first (its share/ directory), a built app has none.
        os.environ["XDG_DATA_DIRS"] = os.path.join(directory, "xdg")
        notebook()
        ipython()
        completions()
