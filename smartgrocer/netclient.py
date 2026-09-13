"""
SmartGrocer - LAN multi-till client.

RemoteConnection makes a network connection to another till's NetDBServer
(see netserver.py) look, to the rest of this codebase, like an ordinary
sqlite3.Connection: the same .execute(...)/.executemany(...)/.fetchone()/
.fetchall()/.commit() calls used everywhere in pos.py, staff.py, reports.py
and every screen in gui/screens.py work completely unchanged whether
self.app.conn is a real local sqlite3.Connection (the Main Till, today's
default and unchanged behavior) or one of these (a Cashier Till talking to
the Main Till over the shop's network). Nothing outside this file and
netserver.py needs to know or care which one it's holding.
"""

from __future__ import annotations

import http.client
import json
import ssl
from typing import Any, Iterable, Optional


class RemoteError(Exception):
    """The Main Till couldn't be reached, or rejected the request (wrong
    pairing code, session expired). Not a data problem - a connectivity one."""


class RemoteIntegrityError(ValueError):
    """A constraint the Main Till's database enforces was violated (e.g. a
    duplicate barcode) - raised as a ValueError specifically so it flows
    into the same `except ValueError` error dialogs that already handle
    this locally (pos.add_product, pos.receive_stock, etc.) without every
    call site needing to know whether it's talking to a local or remote
    database."""


class RemoteRow:
    """Stands in for sqlite3.Row - supports row["col"], dict(row), and
    iterating the values, which is all this codebase ever does with a row."""

    def __init__(self, data: dict):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        return self._data.keys()

    def __iter__(self):
        return iter(self._data.values())

    def __repr__(self):
        return f"RemoteRow({self._data!r})"


class RemoteCursor:
    """Stands in for sqlite3.Cursor - only what this codebase actually
    uses: .lastrowid, .rowcount, .fetchone(), .fetchall()."""

    def __init__(self, lastrowid=None, rowcount: int = -1,
                 rows: Optional[list[RemoteRow]] = None):
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._rows = rows or []
        self._pos = 0

    def fetchone(self) -> Optional[RemoteRow]:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[RemoteRow]:
        rest = self._rows[self._pos:]
        self._pos = len(self._rows)
        return rest

    def __iter__(self):
        # sqlite3.Cursor supports `for row in conn.execute(...)` directly,
        # without an explicit .fetchall() first - matching that here in
        # case anything relies on it, even though everything in this
        # codebase that runs on a Cashier Till currently calls .fetchall()
        # or .fetchone() explicitly (schema init/migration, the one place
        # that iterates a cursor directly, only ever runs against the Main
        # Till's own local connection, never a remote one).
        return self

    def __next__(self) -> RemoteRow:
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class RemoteConnection:
    """One logical session against a NetDBServer, over HTTPS. Created once
    (at app startup, or the moment this till is switched into "Cashier
    Till" mode from the Network screen) and reused for the life of the
    app, exactly like a local sqlite3 connection is today."""

    row_factory = None  # rows are always RemoteRow regardless; present so any code that merely checks this attribute doesn't fail

    def __init__(self, host: str, port: int, pairing_code: str, timeout: float = 8.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._session_id: Optional[str] = None
        self._connect(pairing_code)

    def _request(self, path: str, payload: dict) -> dict:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # self-signed cert on a private LAN - same trust model as the phone scanner
        try:
            conn = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout, context=ctx)
            body = json.dumps(payload).encode("utf-8")
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read() or b"{}")
            conn.close()
        except RemoteError:
            raise
        except Exception as e:
            raise RemoteError(f"Can't reach the Main Till at {self.host}:{self.port} - {e}")
        return data

    def _connect(self, pairing_code: str):
        result = self._request("/connect", {"code": pairing_code})
        if not result.get("ok"):
            error = result.get("error", "could not connect to the Main Till")
            if error == "wrong_pairing_code":
                error = "That pairing code was rejected - check it against the Main Till's Network screen."
            raise RemoteError(error)
        self._session_id = result["session_id"]

    def _raise_for_error(self, result: dict):
        error = result.get("error", "remote database error")
        if result.get("kind") == "integrity":
            raise RemoteIntegrityError(error)
        raise RemoteError(error)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> RemoteCursor:
        # The rest of the codebase always follows a SELECT/PRAGMA with
        # .fetchone() or .fetchall() - fetching the full result here lets
        # the returned cursor answer either one without a second round trip.
        stripped = sql.strip().upper()
        fetch = "all" if (stripped.startswith("SELECT") or stripped.startswith("PRAGMA")) else "none"
        result = self._request("/exec", {
            "session_id": self._session_id, "sql": sql, "params": list(params), "fetch": fetch,
        })
        if not result.get("ok"):
            self._raise_for_error(result)
        rows = [RemoteRow(r) for r in result.get("rows", [])] if fetch == "all" else []
        return RemoteCursor(lastrowid=result.get("lastrowid"), rowcount=result.get("rowcount", -1), rows=rows)

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> RemoteCursor:
        result = self._request("/execmany", {
            "session_id": self._session_id, "sql": sql,
            "seq_of_params": [list(p) for p in seq_of_params],
        })
        if not result.get("ok"):
            self._raise_for_error(result)
        return RemoteCursor(lastrowid=result.get("lastrowid"), rowcount=result.get("rowcount", -1))

    def commit(self):
        result = self._request("/commit", {"session_id": self._session_id})
        if not result.get("ok"):
            self._raise_for_error(result)

    def rollback(self):
        try:
            self._request("/rollback", {"session_id": self._session_id})
        except RemoteError:
            pass  # best-effort - a connection that's already lost isn't worth raising over on the way out

    def close(self):
        try:
            self._request("/disconnect", {"session_id": self._session_id})
        except RemoteError:
            pass

    def ping(self) -> bool:
        """Cheap reachability check for the Network screen's status
        display - does NOT use the session, so it still answers even if
        the session id has expired server-side."""
        try:
            result = self._request("/ping", {})
            return bool(result.get("ok"))
        except RemoteError:
            return False
