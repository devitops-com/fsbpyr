"""A small FastAPI application."""

from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, WebSocket
from pydantic import BaseModel, Field

app = FastAPI(title="corpus", version="1.0.0")
AUDIT: list[str] = []


class Item(BaseModel):
    name: str = Field(min_length=2)
    price: float = Field(gt=0)
    tags: list[str] = []


def greeting() -> str:
    return "hello"


def audit(entry: str) -> None:
    AUDIT.append(entry)


@app.get("/hello/{who}")
def hello(who: str, greet: Annotated[str, Depends(greeting)]) -> dict[str, str]:
    return {"message": f"{greet} {who}"}


@app.post("/items", status_code=201)
def create_item(item: Item, tasks: BackgroundTasks) -> Item:
    tasks.add_task(audit, item.name)
    return item


@app.websocket("/ws")
async def echo(websocket: WebSocket) -> None:
    await websocket.accept()
    text = await websocket.receive_text()
    await websocket.send_text(f"echo {text}")
    await websocket.close()
