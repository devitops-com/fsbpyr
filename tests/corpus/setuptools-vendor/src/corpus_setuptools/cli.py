"""Uses setuptools' own API, which imports the libraries it vendors (RT-4)."""

import importlib

from setuptools import Distribution, find_packages


def main() -> None:
    dist = Distribution({"name": "demo", "version": "1.2.3", "packages": find_packages()})
    dist.finalize_options()
    print(dist.get_name(), dist.get_version(), type(dist).__module__)
    version = importlib.import_module("distutils.version")  # after setuptools: it maps distutils
    print(version.LooseVersion("1.2.3") < version.LooseVersion("1.10"))
