"""Async SQLAlchemy over aiosqlite, redis against fakeredis, and bson's C extension."""

import asyncio
import datetime
import tempfile
from decimal import Decimal

import bson
import fakeredis
from bson.decimal128 import Decimal128
from bson.objectid import ObjectId
from sqlalchemy import ForeignKey, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]


class Book(Base):
    __tablename__ = "books"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))


async def sql(directory: str) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory}/db.sqlite")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([Author(id=1, name="Ada"), Author(id=2, name="Brian")])
        session.add_all([Book(title="Notes", author_id=1), Book(title="Engines", author_id=1),
                         Book(title="Kernels", author_id=2)])  # fmt: skip
        await session.commit()
        joined = await session.execute(
            select(Author.name, Book.title).join(Book).order_by(Author.name, Book.title)
        )
        print("sql join", [tuple(row) for row in joined])
        book = await session.scalar(select(Book).where(Book.title == "Notes"))
        book.title = "Collected Notes"
        await session.commit()
        print("sql titles", sorted(await session.scalars(select(Book.title))))
    await engine.dispose()


def redis_part() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    client.set("greeting", "hello")
    client.hset("user:1", mapping={"name": "Ada", "role": "admin"})
    with client.pipeline() as pipe:
        pipe.incr("counter").incr("counter").get("greeting")
        print("redis pipeline", pipe.execute())
    print("redis hash", sorted(client.hgetall("user:1").items()))
    pubsub = client.pubsub()
    pubsub.subscribe("news")
    client.publish("news", "first")
    messages = [pubsub.get_message(timeout=1) for _ in range(2)]
    print("redis pubsub", [(m["type"], m["data"]) for m in messages if m])


def bson_part() -> None:
    document = {
        "_id": ObjectId("507f1f77bcf86cd799439011"),
        "when": datetime.datetime(2024, 5, 6, 7, 8, 9, tzinfo=datetime.UTC),
        "price": Decimal128(Decimal("19.99")),
    }
    data = bson.encode(document)
    print("bson size", len(data), data.hex()[:24])
    print("bson roundtrip", bson.decode(data))
    print("bson has_c", bson.has_c())


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        asyncio.run(sql(directory))
    redis_part()
    bson_part()
