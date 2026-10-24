"""Builds an onnx graph, checks it, runs it with onnx's reference evaluator and onnxruntime."""

import tempfile
from pathlib import Path

import numpy as np
import onnx
import onnxruntime as ort
from onnx import TensorProto, checker, helper, numpy_helper, shape_inference
from onnx.reference import ReferenceEvaluator


def build_model() -> onnx.ModelProto:
    weights = numpy_helper.from_array(np.array([[1, -1, 2], [0.5, 1, -2]], np.float32), "w")
    bias = numpy_helper.from_array(np.array([0.5, -1, 0], np.float32), "b")
    graph = helper.make_graph(
        [
            helper.make_node("MatMul", ["x", "w"], ["m"]),
            helper.make_node("Add", ["m", "b"], ["a"]),
            helper.make_node("Relu", ["a"], ["y"]),
        ],
        "mlp",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, ["n", 2])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, ["n", 3])],
        [weights, bias],
    )  # fmt: skip
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    return model


def rounded(array: np.ndarray) -> list:
    return np.round(array, 4).tolist()


def main() -> None:
    model = build_model()
    checker.check_model(model)
    inferred = shape_inference.infer_shapes(model)
    print("inferred", [(v.name, v.type.tensor_type.shape.dim[1].dim_value)
                       for v in inferred.graph.value_info])  # fmt: skip
    x = np.array([[1, 2], [-1, 0.5], [3, -3]], np.float32)
    print("reference", rounded(ReferenceEvaluator(model).run(None, {"x": x})[0]))
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "mlp.onnx"
        onnx.save(model, path)
        loaded = onnx.load(path)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        session = ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
    print("loaded nodes", [node.op_type for node in loaded.graph.node])
    print("providers", session.get_providers())
    print("onnxruntime", rounded(session.run(None, {"x": x})[0]))
