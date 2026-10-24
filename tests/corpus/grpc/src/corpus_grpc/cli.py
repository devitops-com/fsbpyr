"""gRPC over TLS: code generated from a shipped .proto at run time by grpc_tools.protoc."""

import datetime
import importlib.resources
import ipaddress
import sys
import tempfile
from concurrent import futures
from pathlib import Path

import grpc
import grpc_tools
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from google.protobuf.timestamp_pb2 import Timestamp
from grpc_tools import protoc

AT = Timestamp(seconds=1_700_000_000)


def make_certificate() -> tuple[bytes, bytes]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "corpus.test")])
    start = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=36500))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("corpus.test"),
                       x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))


def generate(out: Path):
    """Compiles greeter.proto into ``out`` and imports the generated modules."""
    proto = importlib.resources.files("corpus_grpc") / "greeter.proto"
    well_known = Path(grpc_tools.__file__).parent / "_proto"  # google/protobuf/*.proto
    with importlib.resources.as_file(proto) as proto_path:
        code = protoc.main(["protoc", f"-I{proto_path.parent}", f"-I{well_known}",
                            f"--python_out={out}", f"--grpc_python_out={out}", str(proto_path)])
    print("protoc exit", code)
    sys.path.insert(0, str(out))
    import greeter_pb2
    import greeter_pb2_grpc
    return greeter_pb2, greeter_pb2_grpc


def run(pb2, pb2_grpc) -> None:
    class Greeter(pb2_grpc.GreeterServicer):
        def SayHello(self, request, context):
            return pb2.HelloReply(message=f"Hello, {request.name}!", at=AT)

        def SayMany(self, request, context):
            for index in range(request.count):
                yield pb2.HelloReply(message=f"{request.name} #{index}", at=AT)

    cert, key = make_certificate()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    pb2_grpc.add_GreeterServicer_to_server(Greeter(), server)
    port = server.add_secure_port("127.0.0.1:0", grpc.ssl_server_credentials([(key, cert)]))
    server.start()
    try:
        credentials = grpc.ssl_channel_credentials(root_certificates=cert)
        options = [("grpc.ssl_target_name_override", "corpus.test")]
        with grpc.secure_channel(f"127.0.0.1:{port}", credentials, options) as channel:
            stub = pb2_grpc.GreeterStub(channel)
            reply = stub.SayHello(pb2.HelloRequest(name="Ada"), timeout=30)
            print("unary", reply.message, reply.at.seconds)
            for reply in stub.SayMany(pb2.HelloRequest(name="Grace", count=3), timeout=30):
                print("stream", reply.message)
    finally:
        server.stop(grace=1).wait()
    print("server stopped")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run(*generate(Path(tmp)))
