"""Messaging libraries: zmq sockets (libzmq bundled in the wheel), msgpack, avro, CBOR."""

import datetime
import io

import cbor2
import fastavro
import msgpack
import zmq

SCHEMA = {
    "type": "record",
    "name": "Reading",
    "fields": [
        {"name": "sensor", "type": "string"},
        {"name": "value", "type": "double"},
        {"name": "tags", "type": {"type": "array", "items": "string"}},
    ],
}


def zmq_round_trips() -> None:
    print("zmq version", zmq.zmq_version())
    with zmq.Context() as context:
        rep = context.socket(zmq.REP)
        port = rep.bind_to_random_port("tcp://127.0.0.1")
        req = context.socket(zmq.REQ)
        req.connect(f"tcp://127.0.0.1:{port}")
        req.send_json({"ping": 1})
        print("rep got", rep.recv_json())
        rep.send_string("pong")
        print("req got", req.recv_string())
        req.close()
        rep.close()

        pub = context.socket(zmq.PUB)
        pub.bind("inproc://news")
        sub = context.socket(zmq.SUB)
        sub.connect("inproc://news")
        sub.setsockopt_string(zmq.SUBSCRIBE, "weather")
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        for _ in range(50):  # a subscription reaches the publisher a moment after connect
            pub.send_multipart([b"sport", b"ignored"])
            pub.send_multipart([b"weather", b"sunny"])
            if poller.poll(100):
                break
        print("sub got", sub.recv_multipart())
        pub.close()
        sub.close()


def msgpack_ext() -> None:
    def encode(obj):
        if isinstance(obj, complex):
            return msgpack.ExtType(1, msgpack.packb([obj.real, obj.imag]))
        raise TypeError(obj)

    def decode(code, data):
        if code == 1:
            real, imag = msgpack.unpackb(data)
            return complex(real, imag)
        return msgpack.ExtType(code, data)

    packed = msgpack.packb({"z": 1 + 2j, "list": [1, 2.5, None]}, default=encode)
    print("msgpack", len(packed), packed.hex())
    print("msgpack back", msgpack.unpackb(packed, ext_hook=decode))


def avro_round_trip() -> None:
    records = [
        {"sensor": "a", "value": 1.5, "tags": ["x"]},
        {"sensor": "b", "value": -2.0, "tags": []},
    ]
    buffer = io.BytesIO()
    fastavro.writer(buffer, fastavro.parse_schema(SCHEMA), records)
    buffer.seek(0)
    print("avro", list(fastavro.reader(buffer)))


def cbor_round_trip() -> None:
    when = datetime.datetime(2024, 5, 17, 12, 30, tzinfo=datetime.UTC)
    encoded = cbor2.dumps({"when": when, "tagged": cbor2.CBORTag(4000, [1, 2])})
    print("cbor", encoded.hex())
    print("cbor back", cbor2.loads(encoded))


def main() -> None:
    zmq_round_trips()
    msgpack_ext()
    avro_round_trip()
    cbor_round_trip()
