"""``importlib.metadata`` over dist-info directories stored in the payload (plan §3.4)."""

import os
import re
from importlib.metadata import Distribution, DistributionFinder

import _fsbpyr

from .resources import PayloadTraversable

_SEPARATORS = re.compile(r"[-_.]+")


class PayloadDistribution(Distribution):
    def __init__(self, root: str, dist_info: str) -> None:
        self._root = root
        self._dist_info = dist_info
        self._path = PayloadTraversable(root, dist_info)  # as on PathDistribution

    def read_text(self, filename: str | os.PathLike[str]) -> str | None:
        record = _fsbpyr.find_file(f"{self._dist_info}/{os.fspath(filename)}")
        if record is None:
            return None
        return _fsbpyr.read(record[4]).decode("utf-8")

    def locate_file(  # type: ignore[override]  # a Traversable, structurally a SimplePath
        self, path: str | os.PathLike[str]
    ) -> PayloadTraversable:
        return PayloadTraversable(self._root, "").joinpath(os.fspath(path))


def find_distributions(
    root: str, context: DistributionFinder.Context | None = None
) -> list[Distribution]:
    """Payload distributions matching ``context`` (name lookups use the sorted DISTS table)."""
    context = context or DistributionFinder.Context()
    if root not in context.path:
        return []
    if context.name is None:
        records = _fsbpyr.dists()
    else:
        record = _fsbpyr.find_dist(_SEPARATORS.sub("-", context.name).lower())
        records = [record] if record is not None else []
    return [PayloadDistribution(root, record[2]) for record in records]
