"""Canonical marshal encoding of code objects (plan decision D16).

CPython's ``marshal.dumps`` output depends on the state of the process that runs it:
FLAG_REF is set on any object whose refcount is > 1 (Python/marshal.c, ``w_ref``), and whether
a string constant is interned depends on equal strings interned earlier. Builds would therefore
not be reproducible. ``canonicalize`` parses a marshal stream into an object graph (references
resolve to the same node) and re-emits it with a policy that depends only on the graph:

* strings are merged by value; a merged string is interned if any occurrence was interned;
* equal immutable values (numbers, bytes, strings, and tuples and frozensets of them) are one
  node: whether compile() shared an equal constant or made two depends on the strings the
  process interned before (a build's worker processes each have their own history, D39);
* FLAG_REF is set iff a node occurs at least twice, or it is an interned string;
* reference indices are reassigned in pre-order, the order the reader uses;
* set/frozenset elements are sorted by their canonical standalone encoding.

``marshal.loads`` of the canonical stream yields objects equal to the original. Supported
formats: marshal version 4 as written by CPython 3.12 and 3.13, version 5 (which adds the slice
type) as written by 3.14. The code object layout is the same in all three; each fsbpyr handles
the version it runs on.
"""

import marshal
import struct
import sys
from types import CodeType

SUPPORTED_PYTHON = frozenset({(3, 12), (3, 13), (3, 14)})
FLAG_REF = 0x80
TYPE_REF = ord("r")
MAX_DEPTH = 100  # nesting limit; compile() output stays far below (stdlib maximum: 11)

_NO_PAYLOAD = frozenset(b"NFTS.")
_FIXED = {ord("i"): 4, ord("g"): 8, ord("y"): 16}
_STRINGS = frozenset(b"zZaAut")
_ASCII_STRINGS = frozenset(b"zZaA")
_INTERNED = frozenset(b"tAZ")
_SHORT = frozenset(b"zZ")
_SEQUENCES = frozenset(b"([<>")
_SETS = frozenset(b"<>")
_I32 = struct.Struct("<i")
_HAS_SLICE = sys.version_info >= (3, 14)  # marshal version 5 added the slice type


class BytecodeError(ValueError):
    """The marshal stream is malformed or uses an unsupported type."""


class _Node:
    __slots__ = ("count", "head", "kids", "tag")

    def __init__(self, tag: int) -> None:
        self.tag = tag  # type code without FLAG_REF
        self.head = b""  # scalar payload written right after the tag
        self.kids: list[_Node | bytes] = []  # child nodes, or raw scalars inside code objects
        self.count = 0


def _string_head(text: bytes, ascii_kind: bool, interned: bool) -> tuple[int, bytes]:
    if ascii_kind and len(text) < 256:
        return ord("Z" if interned else "z"), bytes([len(text)]) + text
    if ascii_kind:
        return ord("A" if interned else "a"), _I32.pack(len(text)) + text
    return ord("t" if interned else "u"), _I32.pack(len(text)) + text


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = memoryview(data)
        self.pos = 0
        self.refs: list[_Node] = []
        self.strings: dict[tuple[bytes, bool], _Node] = {}
        self.in_progress: set[int] = set()

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise BytecodeError("truncated marshal data")
        chunk = bytes(self.data[self.pos : self.pos + n])
        self.pos += n
        return chunk

    def length(self) -> int:
        n: int = _I32.unpack(self.take(4))[0]
        if n < 0:
            raise BytecodeError(f"invalid length {n}")
        return n

    def node(self, depth: int = 0) -> _Node:
        if depth > MAX_DEPTH:
            raise BytecodeError("nesting too deep")
        code = self.take(1)[0]
        tag, flagged = code & ~FLAG_REF, bool(code & FLAG_REF)
        if tag == TYPE_REF:
            return self._reference()
        if tag in _STRINGS:
            return self._string(tag, flagged)
        node = _Node(tag)
        if flagged:  # pre-order index assignment, matching r_ref / r_ref_reserve
            self.refs.append(node)
        self.in_progress.add(id(node))
        self._fill(node, depth)
        self.in_progress.discard(id(node))
        return node

    def _reference(self) -> _Node:
        index: int = _I32.unpack(self.take(4))[0]
        if not 0 <= index < len(self.refs):
            raise BytecodeError(f"invalid reference index {index}")
        target = self.refs[index]
        if id(target) in self.in_progress:
            raise BytecodeError("recursive reference")
        return target

    def _string(self, tag: int, flagged: bool) -> _Node:
        size = self.take(1)[0] if tag in _SHORT else self.length()
        key = (self.take(size), tag in _ASCII_STRINGS)
        node = self.strings.get(key)
        if node is None:
            node = self.strings[key] = _Node(tag)
        interned = tag in _INTERNED or node.tag in _INTERNED
        node.tag, node.head = _string_head(key[0], key[1], interned)
        if flagged:
            self.refs.append(node)
        return node

    def _fill(self, node: _Node, depth: int) -> None:
        tag = node.tag
        if tag in _NO_PAYLOAD:
            return
        if tag in _FIXED:
            node.head = self.take(_FIXED[tag])
        elif tag == ord("l"):
            raw = self.take(4)
            node.head = raw + self.take(2 * abs(_I32.unpack(raw)[0]))
        elif tag == ord("s"):
            size = self.length()
            node.head = _I32.pack(size) + self.take(size)
        elif tag in _SEQUENCES:
            node.kids = [self.node(depth + 1) for _ in range(self.length())]
        elif tag == ord(")"):
            node.kids = [self.node(depth + 1) for _ in range(self.take(1)[0])]
        elif tag == ord(":") and _HAS_SLICE:
            node.kids = [self.node(depth + 1) for _ in range(3)]
        elif tag == ord("c"):
            node.head = self.take(20)  # argcount, posonlyargcount, kwonlyargcount, stacksize, flags
            objects: list[_Node | bytes] = [self.node(depth + 1) for _ in range(8)]
            firstlineno = self.take(4)
            node.kids = [*objects, firstlineno, self.node(depth + 1), self.node(depth + 1)]
        else:
            raise BytecodeError(f"unsupported marshal type {chr(tag)!r}")


class _Writer:
    def __init__(self, set_keys: dict[int, bytes]) -> None:
        self.out = bytearray()
        self.index: dict[int, int] = {}
        self.set_keys = set_keys  # canonical standalone encodings of set elements

    def _set_key(self, kid: _Node | bytes) -> bytes:
        return self.set_keys[id(kid)]

    def emit(self, node: _Node, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise BytecodeError("nesting too deep")
        known = self.index.get(id(node))
        if known is not None:
            self.out.append(TYPE_REF)
            self.out += _I32.pack(known)
            return
        flag = 0
        if node.count >= 2 or node.tag in _INTERNED:
            flag = FLAG_REF
            self.index[id(node)] = len(self.index)
        self.out.append(node.tag | flag)
        self.out += node.head
        kids = node.kids
        if node.tag in _SEQUENCES:
            if node.tag in _SETS:
                kids = sorted(kids, key=self._set_key)
            self.out += _I32.pack(len(kids))
        elif node.tag == ord(")"):
            self.out.append(len(kids))
        for kid in kids:
            if isinstance(kid, bytes):
                self.out += kid
            else:
                self.emit(kid, depth + 1)


def _postorder(root: _Node) -> list[_Node]:
    """Every node once, children before parents (iterative: no recursion limits)."""
    order: list[_Node] = []
    seen: set[int] = set()
    stack: list[tuple[_Node, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if expanded:
            order.append(node)
        elif id(node) not in seen:
            seen.add(id(node))
            stack.append((node, True))
            stack.extend((kid, False) for kid in reversed(node.kids) if isinstance(kid, _Node))
    return order


def _set_keys(root: _Node) -> dict[int, bytes]:
    """Standalone encodings of set elements, computed once each, innermost first."""
    nodes = _postorder(root)
    members = {
        id(kid)
        for node in nodes
        if node.tag in _SETS
        for kid in node.kids
        if isinstance(kid, _Node)
    }
    keys: dict[int, bytes] = {}
    for node in nodes:
        if id(node) in members:
            writer = _Writer(keys)
            writer.emit(node)
            keys[id(node)] = bytes(writer.out)
    return keys


_SCALARS = frozenset(b"igyls")  # int, float, complex, long, bytes: merged by their encoding
_TUPLES = frozenset(b"()")


def _merge(root: _Node) -> None:
    """Equal immutable values become one node, children first: each node's children are
    replaced by the first node of their value."""
    keys: dict[int, object] = {}  # id(node) -> its value, for nodes that are values
    first: dict[object, _Node] = {}
    for node in _postorder(root):
        node.kids = [first.get(keys.get(id(kid)), kid) if isinstance(kid, _Node) else kid
                     for kid in node.kids]  # fmt: skip
        key = _value(node, keys)
        if key is not None:
            keys[id(node)] = key
            first.setdefault(key, node)


def _value(node: _Node, keys: dict[int, object]) -> object:
    """What identifies the value of ``node`` among values, or None (code objects, sets, and
    containers of them; None, True and the like, which marshal never references)."""
    if node.tag in _STRINGS or node.tag in _SCALARS:
        return node.tag, node.head
    if node.tag not in _TUPLES and node.tag != ord(">"):
        return None
    kids = [keys.get(id(kid)) if isinstance(kid, _Node) else None for kid in node.kids]
    if any(kid is None for kid in kids):
        return None
    return ("tuple", tuple(kids)) if node.tag in _TUPLES else ("frozenset", frozenset(kids))


def _count(root: _Node) -> None:
    seen: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        node.count += 1
        if id(node) in seen:
            continue
        seen.add(id(node))
        stack.extend(kid for kid in node.kids if isinstance(kid, _Node))


def canonicalize(data: bytes) -> bytes:
    """Return the canonical form of one marshalled object (the running Python's marshal version)."""
    try:
        reader = _Reader(data)
        root = reader.node()
        if reader.pos != len(data):
            raise BytecodeError("trailing bytes after marshal object")
        _merge(root)
        _count(root)
        writer = _Writer(_set_keys(root))
        writer.emit(root)
    except RecursionError as exc:  # pragma: no cover - MAX_DEPTH keeps recursion shallow
        raise BytecodeError("nesting too deep") from exc
    return bytes(writer.out)


def dumps(code: CodeType) -> bytes:
    """``marshal.dumps`` followed by ``canonicalize``; only for the supported CPython version."""
    if sys.version_info[:2] not in SUPPORTED_PYTHON:  # pragma: no cover - the CLI stops them
        raise BytecodeError("canonical bytecode supports CPython 3.12, 3.13 and 3.14 only")
    return canonicalize(marshal.dumps(code))
