"""gnureadline's extension module, which links GNU readline statically (0.15.2 broke on it)."""

import gnureadline as readline


def main() -> None:
    readline.clear_history()
    for line in ("first", "second"):
        readline.add_history(line)
    print(readline.get_current_history_length(), readline.get_history_item(2))
    readline.set_completer_delims(" ,")
    print(repr(readline.get_completer_delims()))
    print("GNU" in (readline.__doc__ or "") or readline.backend)
