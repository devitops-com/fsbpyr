"""An aiohttp server over TLS (self-signed certificate) and a client that trusts it."""

import asyncio
import datetime
import ipaddress
import ssl
import tempfile
from pathlib import Path

import aiohttp
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def make_certificate(directory: Path) -> tuple[Path, Path]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    start = datetime.datetime(2025, 1, 1, tzinfo=datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(start)
        .not_valid_after(start + datetime.timedelta(days=36500))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                       x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return cert_path, key_path


async def hello(request: web.Request) -> web.Response:
    return web.json_response({"hello": request.match_info["who"], "secure": request.secure})


async def form(request: web.Request) -> web.Response:
    data = await request.post()
    return web.json_response({"fields": sorted(data.items())})


async def echo(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    async for message in ws:
        await ws.send_str(f"echo {message.data}")
    return ws


async def run(cert_path: Path, key_path: Path) -> None:
    app = web.Application()
    app.add_routes([web.get("/hello/{who}", hello), web.post("/form", form), web.get("/ws", echo)])
    server_ssl = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ssl.load_cert_chain(cert_path, key_path)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=server_ssl)
        await site.start()
        port = runner.addresses[0][1]
        client_ssl = ssl.create_default_context(cafile=str(cert_path))
        connector = aiohttp.TCPConnector(ssl=client_ssl)
        async with aiohttp.ClientSession(connector=connector) as session:
            base = f"https://localhost:{port}"
            async with session.get(f"{base}/hello/Ada") as response:
                print("json", response.status, response.content_type, await response.json())
            async with session.post(f"{base}/form", data={"b": "2", "a": "1"}) as response:
                print("form", response.status, await response.json())
            async with session.ws_connect(f"{base}/ws") as ws:
                await ws.send_str("ping")
                print("websocket", (await ws.receive()).data)
                await ws.close()
    finally:
        await runner.cleanup()
    print("server stopped")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(run(*make_certificate(Path(tmp))))
