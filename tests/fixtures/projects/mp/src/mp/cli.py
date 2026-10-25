"""Every multiprocessing start method except fork re-executes ``sys.executable`` (the app)."""

import importlib.util
import multiprocessing
import os
import shlex
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import shared_memory


def square(x: int) -> int:
    return x * x


def _helpers() -> list[list[str]]:
    script = importlib.util.find_spec("mp.helper")  # its file; importing it would run it
    assert script is not None and script.origin is not None
    return [
        ["-u", "-B", "-m", "mp.helper", "a"],
        [script.origin, "c"],  # a script of the payload in a built app (D44)
        ["-c", "import sys; print('code', sys.argv)", "b"],
        ["-c", "exit(4)"],  # site's builtins, which built apps define without site (D14)
    ]


def main() -> None:
    if sys.argv[1:] == ["reexec"]:  # helper interpreters through sys.executable (D23, D44)
        for helper in _helpers():
            child = subprocess.run([sys.executable, *helper], capture_output=True, text=True,
                                   check=False)  # fmt: skip
            print(child.returncode, child.stdout.strip())
        return
    if sys.argv[1:] == ["sibling"]:  # a script of the payload imports a module next to it
        script = importlib.util.find_spec("mp.scripts.a")
        assert script is not None and script.origin is not None
        child = subprocess.run([sys.executable, script.origin], capture_output=True, text=True,
                               check=False)  # fmt: skip
        print(child.returncode, child.stdout.strip(), child.stderr.strip()[-200:])
        return
    if sys.argv[1:] == ["argv0"]:  # the name this start gave the program (D14)
        import argparse

        print(sys.argv[0], argparse.ArgumentParser().prog)
        return
    if sys.argv[1:2] == ["restart"]:  # gunicorn's USR2 and Django's autoreloader do this
        print("argv", sys.argv[1:], flush=True)
        if "MP_RESTARTED" not in os.environ:
            os.environ["MP_RESTARTED"] = "1"
            os.execv(sys.executable, [sys.executable] + sys.argv)  # noqa: S606, RUF005  the idiom as it is written
        return
    if sys.argv[1:] == ["missing"]:  # an uncaught import of a module nobody installed
        import mp_surely_not_installed  # type: ignore[import-not-found]  # noqa: F401
    if sys.argv[1:] == ["nest"]:  # starts itself without end: the instance limit stops it (D45)
        child = subprocess.run([sys.executable, "nest"], capture_output=True, text=True,
                               check=False)  # fmt: skip
        print(child.returncode, child.stdout.strip())
        return
    if sys.argv[1:2] == ["fan"]:  # three copies of itself a level, through a shell (D45)
        depth = max(int(sys.argv[2]), 0)
        command = f"{shlex.quote(sys.executable)} fan {depth - 1}; exit $?"  # sh stays between
        children = [subprocess.Popen(["/bin/sh", "-c", command]) for _ in range(3 if depth else 0)]
        sys.exit(max((child.wait() for child in children), default=0))
    if "--wait" in sys.argv:  # lets a test replace the binary on disk before any child starts
        print("ready", flush=True)
        sys.stdin.readline()
    methods = [arg for arg in sys.argv[1:] if arg != "--wait"]
    for method in methods or ["spawn", "forkserver", "fork"]:
        context = multiprocessing.get_context(method)
        with context.Pool(2) as pool:
            print(method, "pool", pool.map(square, range(5)))
        with ProcessPoolExecutor(2, mp_context=context) as executor:
            print(method, "executor", list(executor.map(square, range(3))))
    memory = shared_memory.SharedMemory(create=True, size=16)  # uses the resource tracker
    memory.buf[:2] = b"ok"
    print("shared_memory", bytes(memory.buf[:2]).decode())
    memory.close()
    memory.unlink()


if __name__ == "__main__":  # entry as a module: children re-import it as __mp_main__
    main()
