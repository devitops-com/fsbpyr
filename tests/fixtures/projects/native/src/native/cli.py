"""Imports each native dependency and prints one line from it."""


def main() -> int:
    import markupsafe
    import orjson

    print("orjson", orjson.dumps({"a": [1, 2]}).decode())
    print("markupsafe", markupsafe.escape("<b>&</b>"))
    return 0
