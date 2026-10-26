import sys

import utils
from helpers import greeting


def main() -> None:
    print(greeting(utils.shout(" ".join(sys.argv[1:]) or "world")))
    print("name", __name__, "args", sys.argv[1:])


if __name__ == "__main__":
    main()
