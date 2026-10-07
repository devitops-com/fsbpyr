"""One check per package that reads data files located next to its own modules."""

import argparse


def _botocore() -> str:
    import botocore.session

    return "botocore " + botocore.session.get_session().get_service_model("s3").service_name


def _pytz() -> str:
    import pytz

    return "pytz " + pytz.timezone("Europe/Zagreb").zone


def _babel() -> str:
    from babel import Locale

    return "babel " + Locale("de").get_display_name("en")


def _matplotlib() -> str:
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots()
    axes.plot([1, 2, 3])
    image = io.BytesIO()
    figure.savefig(image, format="png")
    return f"matplotlib {image.getvalue().startswith(b'\x89PNG')}"


CHECKS = {"botocore": _botocore, "pytz": _pytz, "babel": _babel, "matplotlib": _matplotlib}


def main() -> None:
    parser = argparse.ArgumentParser(prog="datafiles")
    parser.add_argument("check", choices=sorted(CHECKS))
    print(CHECKS[parser.parse_args().check]())
