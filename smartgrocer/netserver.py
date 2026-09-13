"""
SmartGrocer - LAN multi-till server.

Lets more than one cashier till share the SAME live product/stock/sales
data, instead of each till having its own separate SQLite file that only it
can see. One PC (the "Main Till") keeps the real database file and runs
this server; any other till ("Cashier Till") runs the ordinary SmartGrocer
app but points it at the Main Till over the shop's network (wired Ethernet
or Wi-Fi - either works, this doesn't care which) instead of opening a
database file of its own. See netclient.py for the other side of this.

Design notes:

- This exposes raw SQL execute/fetch over the network (see _handle_exec)
  rather than a bespoke endpoint per business operation (one for checkout,
  one for adding a product, etc.). That looks unusual, but it's the only
  way that lets EVERY existing module (pos.py, staff.py, reports.py, every
  screen in gui/screens.py - all of which call conn.execute(...) directly,
  often with ad-hoc queries that aren't behind any shared function) keep
  working completely unchanged on a Cashier Till. The alternative - a named
  network endpoint for every one of those call sites - would be a much
  larger, much easier to get subtly wrong rewrite, and any query someone
  forgot to convert would silently misbehave only on a remote till, which
  is a bad way to find out. This is safe here specifically because the SQL
  text itself is always a fixed string baked into this codebase - only the
  bound `?` parameters are ever user data, exactly as it is locally, so
  nothing new is exposed by carrying that same call over the network. The
  network boundary is guarded the same way the phone-scanner feature guards
  its own endpoint - see the pairing code below.
- One sqlite3 connection PER CONNECTED TILL (a "session"), not one shared
  connection serving everyone. Combined with WAL mode (db.get_conn), this
  gives each till proper transaction isolation - a till mid-checkout with
  an uncommitted change doesn't leak it to another till's queries, and
  reads keep working on other tills while one till is mid-write, matching
  exactly how this already behaves on a single machine with the GUI thread
  and a background report thread each holding their own connection.
- A short pairing code (shown on the Main Till's Network screen, same idea
  as the phone-scanner's) is required once, when a till first connects;
  the session id issued at that point is the bearer credential for every
  request after that.
- Plain request/response over HTTPS (same self-signed-certificate approach
  as mobile_scan.py, and the same reasoning for why: this is a private
  shop network, not the public internet, so there's no real certificate
  authority to get a trusted certificate from) - no persistent WebSocket
  needed since each call here is already a fast, complete round trip.
"""

from __future__ import annotations

import json
import secrets
import ssl
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import db as db_module
from .mobile_scan import get_lan_ip, _ensure_self_signed_cert

# A till that connected and then vanished without disconnecting (closed
# mid-network-drop, crashed, laptop went to sleep) shouldn't hold its
# database connection open forever - it's cleaned up after this long spent
# completely idle.
_SESSION_IDLE_TIMEOUT_SECONDS = 3600

# Fixed, not OS-assigned (port=0) - a Cashier Till's saved connection
# settings (host + port + pairing code) need to still work after the Main
# Till's app is restarted, which a random port every launch would break.
DEFAULT_PORT = 8790


class _Session:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.lock = threading.Lock()
        self.last_used = time.time()


class NetDBServer:
    """Owns a background HTTPS server that lets other tills run SQL against
    this PC's database, each through its own dedicated connection/session."""

    def __init__(self, db_path: Path | str, pairing_code: Optional[str] = None):
        self.db_path = db_path
        # A fixed pairing_code can be passed in so it survives the Main
        # Till's app being restarted - without this, every restart would
        # hand out a new code and every already-configured Cashier Till
        # would need to be re-paired by hand before it could reconnect.
        self.pairing_code = pairing_code or f"{secrets.randbelow(10000):04d}"
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else 0

    @property
    def url(self) -> str:
        return f"https://{get_lan_ip()}:{self.port}/"

    @property
    def connected_till_count(self) -> int:
        with self._sessions_lock:
            return len(self._sessions)

    def start(self, port: int = DEFAULT_PORT) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # a busy shop can generate a request every second or so per till - keep the console quiet

            def _send_json(self, status: int, payload: dict):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, TypeError):
                    self._send_json(400, {"ok": False, "error": "bad_request"})
                    return
                handlers = {
                    "/connect": server._handle_connect,
                    "/exec": server._handle_exec,
                    "/execmany": server._handle_execmany,
                    "/commit": server._handle_commit,
                    "/rollback": server._handle_rollback,
                    "/disconnect": server._handle_disconnect,
                    "/ping": server._handle_ping,
                }
                handler = handlers.get(self.path)
                if handler is None:
                    self.send_error(404)
                    return
                try:
                    self._send_json(200, handler(payload))
                except Exception as e:  # a bug here shouldn't take the whole server (or the Main Till) down
                    self._send_json(200, {"ok": False, "error": str(e)})

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except (ssl.SSLError, ConnectionResetError, OSError):
                    pass  # a till's connection dropping/retrying isn't a server bug

        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        lan_ip = get_lan_ip()
        cert_path, key_path = _ensure_self_signed_cert(lan_ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        self._reap_thread = threading.Thread(target=self._reap_idle_sessions, daemon=True)
        self._reap_thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None
        with self._sessions_lock:
            for sess in self._sessions.values():
                try:
                    sess.conn.close()
                except Exception:
                    pass
            self._sessions.clear()

    def _reap_idle_sessions(self):
        while self.running:
            time.sleep(60)
            cutoff = time.time() - _SESSION_IDLE_TIMEOUT_SECONDS
            with self._sessions_lock:
                stale = [sid for sid, sess in self._sessions.items() if sess.last_used < cutoff]
                for sid in stale:
                    try:
                        self._sessions.pop(sid).conn.close()
                    except Exception:
                        pass

    def _require_session(self, session_id) -> _Session:
        with self._sessions_lock:
            sess = self._sessions.get(session_id)
        if sess is None:
            raise ValueError("Connection to the Main Till was reset - please reconnect.")
        sess.last_used = time.time()
        return sess

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {k: row[k] for k in row.keys()}

    def _handle_ping(self, payload: dict) -> dict:
        return {"ok": True}

    def _handle_connect(self, payload: dict) -> dict:
        if payload.get("code") != self.pairing_code:
            return {"ok": False, "error": "wrong_pairing_code"}
        conn = db_module.get_conn(self.db_path, check_same_thread=False)
        session_id = uuid.uuid4().hex
        with self._sessions_lock:
            self._sessions[session_id] = _Session(conn)
        return {"ok": True, "session_id": session_id}

    def _handle_exec(self, payload: dict) -> dict:
        sess = self._require_session(payload.get("session_id"))
        sql = payload.get("sql", "")
        params = payload.get("params") or []
        fetch = payload.get("fetch", "none")
        with sess.lock:
            try:
                cur = sess.conn.execute(sql, params)
            except sqlite3.IntegrityError as e:
                return {"ok": False, "error": str(e), "kind": "integrity"}
            except sqlite3.Error as e:
                return {"ok": False, "error": str(e), "kind": "sqlite"}
            result = {"ok": True, "lastrowid": cur.lastrowid, "rowcount": cur.rowcount}
            if fetch == "all":
                result["rows"] = [self._row_to_dict(r) for r in cur.fetchall()]
            return result

    def _handle_execmany(self, payload: dict) -> dict:
        sess = self._require_session(payload.get("session_id"))
        sql = payload.get("sql", "")
        seq_of_params = payload.get("seq_of_params") or []
        with sess.lock:
            try:
                cur = sess.conn.executemany(sql, seq_of_params)
            except sqlite3.IntegrityError as e:
                return {"ok": False, "error": str(e), "kind": "integrity"}
            except sqlite3.Error as e:
                return {"ok": False, "error": str(e), "kind": "sqlite"}
            return {"ok": True, "lastrowid": cur.lastrowid, "rowcount": cur.rowcount}

    def _handle_commit(self, payload: dict) -> dict:
        sess = self._require_session(payload.get("session_id"))
        with sess.lock:
            sess.conn.commit()
        return {"ok": True}

    def _handle_rollback(self, payload: dict) -> dict:
        sess = self._require_session(payload.get("session_id"))
        with sess.lock:
            sess.conn.rollback()
        return {"ok": True}

    def _handle_disconnect(self, payload: dict) -> dict:
        session_id = payload.get("session_id")
        with self._sessions_lock:
            sess = self._sessions.pop(session_id, None)
        if sess is not None:
            try:
                sess.conn.close()
            except Exception:
                pass
        return {"ok": True}
