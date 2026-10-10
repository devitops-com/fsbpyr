"""``python -m fsbpyr``."""

import sys

from .cli import main

if __name__ == "__main__":  # not when a worker process imports it (multiprocessing, D39)
    sys.exit(main())
