"""Markers that packaging parses but cannot evaluate: ``~=`` on a string is undefined, ``~=``
needs two release segments, and ``extras`` is a name only lock files know."""

UNEVALUABLE = ('platform_machine ~= "x86_64"', 'python_version ~= "3"', '"x" in extras')
