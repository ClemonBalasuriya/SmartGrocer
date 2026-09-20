"""
SmartGrocer - offline-first Cashier Till (Objective 6: "offline-first POS as
local queue + sync-on-reconnect", not a full multi-device conflict-resolution
system - see the PPRS plan for the scoping decision).

The problem this solves: today (see app.py before this module existed), a
Cashier Till that loses its network connection to the Main Till either
refuses to work, or - the fallback that was in place - silently opens a
throwaway local database with a warning that nothing rung up on it will
ever be merged back. Neither is acceptable for a real shop: Group K's
offline-first-POS literature (Nugraha/Somantri/Rofiqi; Dalupang) treats
"can this till keep selling through a network blip, and reconcile
automatically afterwards" as the whole point.

OfflineConnection is a drop-in replacement for netclient.RemoteConnection -
same .execute()/.executemany()/.commit()/.rollback()/.close()/.ping()
interface, so nothing in pos.py, staff.py, reports.py or any GUI screen
needs to know or care which one it's holding (exactly the design principle
netserver.py/netclient.py already established). It adds one thing on top:
when the Main Till can't be reached, instead of raising, it falls back to
a local SQLite "mirror" database (same schema as the shop's real one, via
db.SCHEMA) so the till keeps working, and queues every write it makes into
an outbox table so they can be replayed onto the Main Till once reconnected.

Design, and its deliberate limits:

- The mirror only ever holds a *fresh snapshot* of the "reference" tables
  needed to keep ringing up sales while offline - products, stock_batches,
  staff, customers, offers, suppliers, shop_settings - refreshed in full
  every time this till (re)connects. It does NOT mirror historical
  invoices/reports data; a cashier can still sell (and see stock update
  from their own offline sales) but can't, say, reprint a receipt from
  before the outage until back online. That's a deliberate scope limit,
  not an oversight - see README.
- Every write made while offline is queued in `_offline_outbox`, grouped by
  the same commit() boundaries the rest of the codebase already uses (e.g.
  pos.create_invoice does several execute()/executemany() calls then ONE
  commit() - that whole group becomes one outbox "batch", replayed as a
  unit so a checkout either lands on the Main Till complete or not at all).
- Sync-on-reconnect replays each batch's statements against the Main Till
  in original order. The one conflict this handles specially: an offline
  invoice's invoice_no (a simple incrementing "INV0000123" counter, not a
  UUID) can collide with one another till or the Main Till itself minted
  for a *different* sale during the same outage - this re-mints a fresh
  invoice_no from the Main Till's current sequence and retries that one
  statement once. Any other conflict (e.g. a duplicate barcode someone
  else already registered) stops the sync where it is and leaves the rest
  of the queue intact for the next attempt, rather than guessing - a full
  automatic conflict-resolution policy for every possible case is the
  "future work", not this.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import db as db_module
from . import netclient

# The tables offline reads/writes need to make sense of "can I sell this,
# to this customer, at this price, and is there stock" - NOT the full
# transactional history (invoices/invoice_items/audit_log/etc.), which
# would make every refresh slow on a shop with a year of real data and
# isn't needed for the till to keep functioning through an outage.
_REFERENCE_TABLES = ["products", "staff", "customers", "offers", "suppliers", "shop_settings", "stock_batches"]

_OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS _offline_outbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     INTEGER NOT NULL,
    seq          INTEGER NOT NULL,
    sql          TEXT NOT NULL,
    params_json  TEXT NOT NULL,
    many         INTEGER NOT NULL DEFAULT 0,
    queued_at    TEXT NOT NULL
);
"""


class OfflineConnection:
    """Talks to the Main Till like netclient.RemoteConnection when it can
    reach it, and transparently falls back to a local mirror + outbox when
    it can't - see module docstring. Constructed the same way as
    RemoteConnection, but never raises: if the Main Till isn't reachable
    yet at construction time, it just starts in offline mode."""

    row_factory = None  # present so any code that merely checks this attribute doesn't fail (matches RemoteConnection)

    def __init__(self, host: str, port: int, pairing_code: str, mirror_path: Path | str):
        self._host = host
        self._port = port
        self._pairing_code = pairing_code
        self._mirror_conn = db_module.get_conn(mirror_path, check_same_thread=False)
        db_module.init_db(self._mirror_conn)
        self._mirror_conn.executescript(_OUTBOX_SCHEMA)
        self._remote: Optional[netclient.RemoteConnection] = None
        self._online = False
        self._batch_id = 0
        self._seq_in_batch = 0
        self.try_reconnect()  # also drains any outbox left over from a previous session, if reachable now

    # ------------------------------------------------------------------ #
    # sqlite3.Connection-shaped interface - see netclient.RemoteConnection
    # ------------------------------------------------------------------ #

    def execute(self, sql: str, params: Iterable[Any] = ()) -> Any:
        params = tuple(params)
        is_write = _is_write(sql)
        if self._online:
            try:
                cur = self._remote.execute(sql, params)
            except netclient.RemoteError:
                self._enter_offline()
                # this one call still fails - the caller's current business
                # transaction (e.g. this checkout attempt) aborts cleanly,
                # exactly as it did before this module existed. The NEXT
                # attempt runs fully offline from statement 1, so nothing
                # ends up half on the Main Till and half in the mirror.
                raise
            if is_write:
                self._mirror_best_effort(lambda: self._mirror_conn.execute(sql, params))
            return cur
        cur = self._mirror_conn.execute(sql, params)
        if is_write:
            self._queue(sql, params, many=False)
        return cur

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> Any:
        seq_of_params = [tuple(p) for p in seq_of_params]
        if self._online:
            try:
                cur = self._remote.executemany(sql, seq_of_params)
            except netclient.RemoteError:
                self._enter_offline()
                raise
            self._mirror_best_effort(lambda: self._mirror_conn.executemany(sql, seq_of_params))
            return cur
        cur = self._mirror_conn.executemany(sql, seq_of_params)
        self._queue(sql, seq_of_params, many=True)
        return cur

    def commit(self) -> None:
        if self._online:
            self._remote.commit()
            self._mirror_best_effort(self._mirror_conn.commit)
        else:
            self._mirror_conn.commit()
            self._batch_id += 1
            self._seq_in_batch = 0

    def rollback(self) -> None:
        if self._online:
            self._remote.rollback()
            self._mirror_best_effort(self._mirror_conn.rollback)
        else:
            self._mirror_conn.rollback()  # also undoes this batch's not-yet-committed outbox rows
            self._seq_in_batch = 0

    def close(self) -> None:
        if self._remote is not None:
            try:
                self._remote.close()
            except Exception:
                pass
        self._mirror_conn.close()

    def ping(self) -> bool:
        """Cheap, non-blocking status check for the UI (Network/POS screens) -
        reports what this connection currently believes, rather than making
        a network call on every screen refresh. See try_reconnect() /
        attempt_sync() for the call that actually re-tests reachability."""
        return self._online

    # ------------------------------------------------------------------ #
    # Offline-specific API used by the GUI (NetworkScreen, POSScreen badge)
    # ------------------------------------------------------------------ #

    @property
    def is_online(self) -> bool:
        return self._online

    @property
    def pending_count(self) -> int:
        """Number of queued offline TRANSACTIONS (commit()-bounded batches),
        not raw statement rows - what a cashier/owner actually cares about."""
        row = self._mirror_conn.execute("SELECT COUNT(DISTINCT batch_id) c FROM _offline_outbox").fetchone()
        return row["c"]

    def try_reconnect(self) -> dict:
        """Attempt to (re)reach the Main Till. If reachable and there's a
        queued outbox (from this call or a previous offline stretch,
        including one from before an app restart - the mirror file
        persists), replay it before declaring this till fully back online.
        Safe to call repeatedly (e.g. from a periodic background timer or
        a "Sync Now" button) - a no-op once already online with an empty
        outbox. Returns {"synced": int, "failed": int, "still_offline": bool}."""
        if self._online and self.pending_count == 0:
            return {"synced": 0, "failed": 0, "still_offline": False}
        try:
            remote = netclient.RemoteConnection(self._host, self._port, self._pairing_code)
        except netclient.RemoteError:
            return {"synced": 0, "failed": 0, "still_offline": True}
        self._remote = remote
        self._online = True
        return self._drain_outbox_then_refresh()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _enter_offline(self) -> None:
        self._online = False
        row = self._mirror_conn.execute("SELECT COALESCE(MAX(batch_id), 0) m FROM _offline_outbox").fetchone()
        self._batch_id = row["m"] + 1
        self._seq_in_batch = 0

    def _mirror_best_effort(self, fn) -> None:
        """Keep the local mirror warm while online, so it's ready the
        instant the network drops - never lets a mirror-side hiccup affect
        the real (remote) operation, since refresh_reference_tables()
        corrects any drift on the next successful reconnect anyway."""
        try:
            fn()
        except Exception:
            pass

    def _queue(self, sql: str, params, many: bool) -> None:
        self._mirror_conn.execute(
            "INSERT INTO _offline_outbox (batch_id, seq, sql, params_json, many, queued_at) VALUES (?,?,?,?,?,?)",
            (self._batch_id, self._seq_in_batch, sql, json.dumps(params), 1 if many else 0,
             datetime.now().isoformat(timespec="seconds")),
        )
        self._seq_in_batch += 1

    def _drain_outbox_then_refresh(self) -> dict:
        """Replay the outbox one BATCH at a time (a batch = everything
        queued between two offline commit() calls, e.g. one whole
        checkout), committing each batch on the Main Till as soon as every
        one of its statements has replayed successfully, before moving to
        the next batch. This keeps each batch's all-or-nothing guarantee
        intact: a batch that fails partway is rolled back on the Main Till
        (undoing just that batch's partial writes, not any earlier batch
        already committed) and the sync stops there, leaving it and
        everything queued after it for the next attempt - never guessing
        past a conflict it doesn't specifically know how to resolve."""
        rows = self._mirror_conn.execute(
            "SELECT id, batch_id, sql, params_json, many FROM _offline_outbox ORDER BY batch_id, seq"
        ).fetchall()
        batch_order: list[int] = []
        batches: dict[int, list] = {}
        for r in rows:
            if r["batch_id"] not in batches:
                batches[r["batch_id"]] = []
                batch_order.append(r["batch_id"])
            batches[r["batch_id"]].append(r)

        synced_count = 0
        stopped = False
        for batch_id in batch_order:
            try:
                for row in batches[batch_id]:
                    params = json.loads(row["params_json"])
                    try:
                        if row["many"]:
                            self._remote.executemany(row["sql"], params)
                        else:
                            self._remote.execute(row["sql"], params)
                    except netclient.RemoteIntegrityError as e:
                        if row["many"] or not _is_invoice_no_collision(row["sql"], e):
                            raise  # a real conflict (e.g. duplicate barcode) - don't guess
                        new_no = _remint_invoice_no(self._remote)
                        params[0] = new_no
                        self._remote.execute(row["sql"], params)
            except (netclient.RemoteError, netclient.RemoteIntegrityError):
                try:
                    self._remote.rollback()  # undo only THIS batch's partial writes
                except netclient.RemoteError:
                    pass
                stopped = True
                break
            else:
                self._remote.commit()
                ids = [row["id"] for row in batches[batch_id]]
                placeholders = ",".join("?" * len(ids))
                self._mirror_conn.execute(f"DELETE FROM _offline_outbox WHERE id IN ({placeholders})", ids)
                self._mirror_conn.commit()
                synced_count += len(ids)

        remaining = self.pending_count
        if remaining == 0 and not stopped:
            self.refresh_reference_tables()
            return {"synced": synced_count, "failed": 0, "still_offline": False}
        # Sync stalled partway (Main Till dropped again, or hit a conflict
        # it won't guess past) - stay offline with whatever's left queued.
        try:
            self._remote.close()
        except Exception:
            pass
        self._remote = None
        self._online = False
        return {"synced": synced_count, "failed": remaining, "still_offline": True}

    def refresh_reference_tables(self) -> None:
        """Pull a fresh full copy of the reference tables (see
        _REFERENCE_TABLES) from the Main Till into the local mirror. Called
        automatically right after (re)connecting with an empty outbox -
        never call this with a non-empty outbox, since it wipes local
        table contents the outbox's own not-yet-synced rows might still
        depend on for reads."""
        if not self._online:
            return
        # Refreshing every table's full contents in one pass necessarily
        # deletes a parent row (e.g. a product) an instant before
        # re-inserting it, while a child row (e.g. a stock batch) elsewhere
        # in this same refresh still references its old id - harmless once
        # the whole refresh finishes, but a violation to STRICT, statement-
        # by-statement foreign-key checking. Disabled for just this bulk
        # resync (SQLite requires no open transaction to toggle it, and
        # nothing here leaves one open across calls) and restored after.
        self._mirror_conn.execute("PRAGMA foreign_keys = OFF")
        try:
            for table in _REFERENCE_TABLES:
                rows = self._remote.execute(f"SELECT * FROM {table}").fetchall()
                self._mirror_conn.execute(f"DELETE FROM {table}")
                if rows:
                    cols = list(rows[0].keys())
                    self._mirror_conn.executemany(
                        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [[r[c] for c in cols] for r in rows],
                    )
            # Seed just the single latest real invoice (not the whole history -
            # see module docstring) so SQLite's own autoincrement sequence, and
            # pos._next_invoice_no's "highest id" query, both continue from the
            # right place if this till goes offline again.
            latest = self._remote.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 1").fetchone()
            self._mirror_conn.execute("DELETE FROM invoices")
            if latest is not None:
                cols = list(latest.keys())
                self._mirror_conn.execute(
                    f"INSERT INTO invoices ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [latest[c] for c in cols],
                )
            self._mirror_conn.commit()
        finally:
            self._mirror_conn.execute("PRAGMA foreign_keys = ON")


def _is_write(sql: str) -> bool:
    stripped = sql.strip().upper()
    return not (stripped.startswith("SELECT") or stripped.startswith("PRAGMA"))


def _is_invoice_no_collision(sql: str, error: Exception) -> bool:
    return "INSERT INTO INVOICES" in sql.upper() and "invoice_no" in str(error) and "invoices" in str(error)


def _remint_invoice_no(remote: netclient.RemoteConnection) -> str:
    """Mirrors pos._next_invoice_no's logic exactly (duplicated rather than
    imported, to avoid a pos<->offline_sync circular import for four lines
    of code) - only ever used for the one retry in _drain_outbox_then_refresh."""
    row = remote.execute("SELECT invoice_no FROM invoices ORDER BY id DESC LIMIT 1").fetchone()
    if row is None:
        return "INV0000001"
    last_n = int(row["invoice_no"].replace("INV", ""))
    return f"INV{last_n + 1:07d}"


def mirror_db_path(db_path: Path | str) -> Path:
    """Where this till's offline mirror lives - alongside its own would-be
    local database, so it's covered by the same backup/.gitignore rules."""
    return Path(db_path).parent / "offline_mirror.db"
