"""Each subcommand imports one heavy native dependency only when it runs (manifest §14)."""

import argparse


def _numpy() -> str:
    import numpy as np

    return f"numpy {np.arange(6).reshape(2, 3).sum(axis=0).tolist()} {np.linalg.det(np.eye(2))}"


def _orjson() -> str:
    import orjson

    return f"orjson {orjson.dumps({'a': [1, 2]}).decode()}"


def _crypto() -> str:
    from cryptography.fernet import Fernet

    fernet = Fernet(Fernet.generate_key())
    return f"cryptography {fernet.decrypt(fernet.encrypt(b'secret')).decode()}"


def _pydantic() -> str:
    from pydantic import BaseModel

    class Point(BaseModel):
        x: int
        y: int

    return f"pydantic {Point(x='1', y=2).model_dump()}"


def _onnx() -> str:
    from importlib.resources import files

    import numpy as np
    import onnxruntime as ort

    model = files("heavy").joinpath("relu_matmul.onnx").read_bytes()  # relu(x) @ w
    session = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    (y,) = session.run(None, {"x": np.array([[1.0, -2.0, 3.0]], dtype=np.float32)})
    return f"onnxruntime {y.tolist()}"


COMMANDS = {"np": _numpy, "json": _orjson, "crypto": _crypto, "model": _pydantic, "onnx": _onnx}


def main() -> None:
    parser = argparse.ArgumentParser(prog="heavy", description="Light CLI, heavy optional deps.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    print(COMMANDS[parser.parse_args().command]())
