"""Every multiprocessing start method except fork re-executes ``sys.executable`` (the app)."""

import multiprocessing
import sys
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import shared_memory


def square(x: int) -> int:
    return x * x


def main() -> None:
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
