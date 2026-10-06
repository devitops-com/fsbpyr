"""Prototype: canonicalize a CPython 3.14 marshal stream (marshal version 5).

CPython's writer sets FLAG_REF on any object whose refcount is > 1 (plus all interned strings),
so the bytes depend on what else is alive in the compiling process. This rewriter parses the
stream into a node graph (refs resolve to the same node) and re-emits it with a policy that
depends only on the graph:
  * FLAG_REF  iff the node occurs >= 2 times in the graph, or it is an interned string;
  * reference indices are reassigned in pre-order (same order the reader uses);
  * frozenset/set elements are sorted by their canonical standalone encoding;
  * strings are merged by value and count as interned if any occurrence was interned
    (whether CPython interns a constant depends on strings already interned in the process).
"""

import struct

FLAG_REF = 0x80
TYPE_REF = ord("r")
INTERNED_TAGS = {ord("t"), ord("A"), ord("Z")}
NO_PAYLOAD = set(b"NFTS.0")
FIXED = {ord("i"): 4, ord("g"): 8, ord("y"): 16}
SIZED32 = set(b"suta")
SIZED8 = set(b"zZ")
SEQ32 = set(b"([<>")


class Node:
    __slots__ = ("count", "head", "kids", "tag")

    def __init__(self, tag: int, head: bytes = b"", kids: list | None = None) -> None:
        self.tag = tag        # type code without FLAG_REF
        self.head = head      # raw scalar payload written right after the tag
        self.kids = kids or []  # child nodes (or ints for code-object scalar fields)
        self.count = 0


STRING_TAGS = set(b"zZaAut")


def _string_head(text: bytes, ascii_kind: bool, interned: bool) -> tuple[int, bytes]:
    if ascii_kind and len(text) < 256:
        return ord("Z" if interned else "z"), bytes([len(text)]) + text
    tag = ("A" if interned else "a") if ascii_kind else ("t" if interned else "u")
    return ord(tag), struct.pack("<i", len(text)) + text


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos, self.refs = memoryview(data), 0, []
        self.strings: dict[tuple[bytes, bool], Node] = {}

    def string(self, tag: int, flagged: bool) -> Node:
        size = self.take(1)[0] if tag in SIZED8 else self.i32()
        key = (self.take(size), tag in b"zZaA")
        node = self.strings.get(key)
        if node is None:
            node = self.strings[key] = Node(tag)
        if tag in INTERNED_TAGS or node.tag in INTERNED_TAGS:
            node.tag, node.head = _string_head(key[0], key[1], True)
        else:
            node.tag, node.head = _string_head(key[0], key[1], False)
        if flagged:
            self.refs.append(node)
        return node

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ValueError("truncated marshal data")
        chunk = bytes(self.data[self.pos : self.pos + n])
        self.pos += n
        return chunk

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def node(self) -> Node:
        code = self.take(1)[0]
        tag, flagged = code & ~FLAG_REF, bool(code & FLAG_REF)
        if tag == TYPE_REF:
            return self.refs[self.i32()]
        if tag in STRING_TAGS:
            return self.string(tag, flagged)
        node = Node(tag)
        if flagged:  # pre-order index assignment, matching r_ref / r_ref_reserve
            self.refs.append(node)
        if tag in NO_PAYLOAD:
            pass
        elif tag in FIXED:
            node.head = self.take(FIXED[tag])
        elif tag == ord("l"):
            raw = self.take(4)
            ndigits = abs(struct.unpack("<i", raw)[0])
            node.head = raw + self.take(2 * ndigits)
        elif tag == ord("s"):
            raw = self.take(4)
            node.head = raw + self.take(struct.unpack("<i", raw)[0])
        elif tag in SEQ32:
            n = self.i32()
            node.kids = [self.node() for _ in range(n)]
        elif tag == ord(")"):
            n = self.take(1)[0]
            node.kids = [self.node() for _ in range(n)]
        elif tag == ord(":"):
            node.kids = [self.node() for _ in range(3)]
        elif tag == ord("c"):
            node.head = self.take(20)  # argcount, posonly, kwonly, stacksize, flags
            objs = [self.node() for _ in range(8)]  # code..qualname
            firstlineno = self.take(4)
            node.kids = objs + [firstlineno, self.node(), self.node()]
        else:
            raise ValueError(f"unsupported marshal type {chr(tag)!r}")
        return node


class _Writer:
    def __init__(self) -> None:
        self.out, self.index = bytearray(), {}

    def emit(self, node: Node) -> None:
        idx = self.index.get(id(node))
        if idx is not None:
            self.out += bytes([TYPE_REF]) + struct.pack("<i", idx)
            return
        flag = 0
        if node.count >= 2 or node.tag in INTERNED_TAGS:
            flag = FLAG_REF
            self.index[id(node)] = len(self.index)
        self.out.append(node.tag | flag)
        self.out += node.head
        kids = node.kids
        if node.tag in SEQ32:
            if node.tag in (ord("<"), ord(">")):
                kids = sorted(kids, key=standalone)
            self.out += struct.pack("<i", len(kids))
        elif node.tag == ord(")"):
            self.out.append(len(kids))
        for kid in kids:
            if isinstance(kid, bytes):
                self.out += kid
            else:
                self.emit(kid)


def _count(node: Node, seen: set[int]) -> None:
    node.count += 1
    if id(node) in seen:
        return
    seen.add(id(node))
    for kid in node.kids:
        if isinstance(kid, Node):
            _count(kid, seen)


def standalone(node: Node) -> bytes:
    """Canonical encoding of a subtree on its own (used as the frozenset sort key)."""
    w = _Writer()
    w.emit(node)
    return bytes(w.out)


def canonicalize(data: bytes) -> bytes:
    reader = _Reader(data)
    root = reader.node()
    if reader.pos != len(data):
        raise ValueError("trailing bytes after marshal object")
    _count(root, set())
    return standalone(root)
