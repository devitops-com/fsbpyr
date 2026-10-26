"""Two commands of one executable; each prints its own name and the arguments it got."""

import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context


def square(x: int) -> int:
    return x * x


def _show(command: str) -> None:
    print(command, [os.path.basename(sys.argv[0]), *sys.argv[1:]])


def _sibling(*args: str) -> None:
    """Start the other command's link, beside this executable, with ``args``."""
    link = os.path.join(os.path.dirname(sys.executable), "multi-beta")
    child = subprocess.run([link, *args], capture_output=True, text=True, check=False)
    print(child.returncode, child.stdout.strip(), child.stderr.strip()[-200:])


def alpha() -> None:
    args = sys.argv[1:]
    if args[:1] == ["sibling"]:  # a command starting a sibling command with these arguments
        _sibling(*args[1:])
    elif args[:1] == ["self"]:  # sys.executable as a helper interpreter (D44)
        child = subprocess.run([sys.executable, "-m", "x"], capture_output=True, text=True,
                               check=False)  # fmt: skip
        print(child.returncode, child.stderr.strip()[:60])
    else:
        _show("alpha")


def beta() -> None:
    args = sys.argv[1:]
    if args[:1] == ["mp"]:  # every start method from a link-named command
        for method in args[1:]:
            context = get_context(method)
            with context.Pool(2) as pool:
                print(method, "pool", pool.map(square, range(4)))
            with ProcessPoolExecutor(2, mp_context=context) as executor:
                print(method, "executor", list(executor.map(square, range(3))))
    else:
        _show("beta")
