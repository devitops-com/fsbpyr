"""Runs the application through TestClient, then under uvicorn."""

import asyncio
import threading
import time

import httpx
import uvicorn
import websockets
from fastapi.testclient import TestClient

from corpus_fastapi.app import AUDIT, app


def in_process() -> None:
    with TestClient(app) as client:
        print("hello", client.get("/hello/Ada").json())
        created = client.post("/items", json={"name": "pen", "price": 1.5, "tags": ["a"]})
        print("create", created.status_code, created.json())
        bad = client.post("/items", json={"name": "x", "price": -1})
        print("invalid", bad.status_code)
        for error in bad.json()["detail"]:
            print("  error", error["loc"], error["type"])
        print("background", AUDIT)
        with client.websocket_connect("/ws") as ws:
            ws.send_text("in-process")
            print("websocket", ws.receive_text())
    schema = app.openapi()
    print("openapi", schema["openapi"], sorted(schema["paths"]))
    print("schema Item", sorted(schema["components"]["schemas"]["Item"]["properties"]))


async def exchange(port: int) -> None:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"http://127.0.0.1:{port}/hello/Grace")
        print("uvicorn http", response.status_code, response.json())
    async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
        await ws.send("over tcp")
        print("uvicorn websocket", await ws.recv())


def serve() -> None:
    config = uvicorn.Config("corpus_fastapi.app:app", host="127.0.0.1", port=0,
                            loop="uvloop", http="httptools", ws="websockets",
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run)
    thread.start()
    try:
        while not server.started:
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        asyncio.run(exchange(port))
    finally:
        server.should_exit = True
        thread.join()
    print("uvicorn stopped", not thread.is_alive())


def main() -> None:
    in_process()
    serve()
