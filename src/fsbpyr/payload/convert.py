"""Turn a payload back into a spec, keeping every stored blob byte-for-byte (no recompression);
a blob inside a block comes back as its raw content, which the writer packs again."""

from .format import NONE, Codec
from .reader import Payload
from .spec import Blob, Dist, File, Module, PayloadSpec


def _blob(payload: Payload, index: int) -> Blob | None:
    if index == NONE:
        return None
    record = payload.blob(index)
    digest = None if record.digest == NONE else payload.digest(record.digest)
    if record.codec is Codec.BLOCK:
        return Blob(payload.read_blob(index), record.size, Codec.NONE, digest)
    return Blob(payload.stored(index), record.size, record.codec, digest)


def spec_from_payload(payload: Payload) -> PayloadSpec:
    files = payload.files()
    dists = payload.dists()

    def dist_name(index: int) -> str | None:
        return None if index == NONE else dists[index].name

    return PayloadSpec(
        python_hexversion=payload.python_hexversion,
        bytecode_magic=payload.bytecode_magic,
        generation_id=payload.generation_id,
        app_name=payload.app_name,
        entrypoint=payload.entrypoint,
        build_info=payload.build_info,
        modules=tuple(
            Module(
                m.name,
                m.path,
                m.kind,
                code=_blob(payload, m.code),
                source=_blob(payload, m.source),
                file=None if m.file == NONE else files[m.file].path,
                dist=dist_name(m.dist),
                flags=m.flags,
            )
            for m in payload.modules()
        ),
        files=tuple(
            File(
                f.path,
                f.kind,
                _required(_blob(payload, f.blob)),
                mode=f.mode,
                flags=f.flags,
                dist=dist_name(f.dist),
                closure=tuple(files[i].path for i in payload.closure(f.closure))
                if f.closure != NONE
                else (),
            )
            for f in files
        ),
        dists=tuple(Dist(d.name, d.dist_info, d.version) for d in dists),
        blocks=any(b.codec is Codec.BLOCK for b in payload.blobs()),
    )


def _required(blob: Blob | None) -> Blob:
    if blob is None:  # pragma: no cover - the reader guarantees every file has a blob
        raise AssertionError("file without blob")
    return blob
