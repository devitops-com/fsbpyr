"""psycopg's binary wheel bundles libpq, libssl and libcrypto; no server is needed here."""

import psycopg
from psycopg import pq, sql


def main() -> None:
    print(pq.__impl__, pq.version() >= 140000)
    query = sql.SQL("select {} from {}").format(sql.Identifier("a b"), sql.Identifier("t"))
    print(query.as_string())
    print(pq.Escaping().escape_string(b"it's"))
    print(psycopg.adapters.types["int4"].oid)
