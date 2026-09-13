"""
SmartGrocer - this till's networking role, saved between launches.

Three modes:
 - "standalone" (default) - exactly today's behavior, a local database file
   only this PC uses. Nothing about multi-till changes for a shop that
   never touches the Network screen.
 - "server" - this till keeps its local database file as the real one, and
   also runs netserver.NetDBServer so other tills can connect to it. Set
   from the Network screen's "Share this till's data" button.
 - "client" - this till has no database of its own; it connects to another
   till's server over the network instead. Set from the Network screen's
   "Connect to another till" button, which fills in host/port/pairing_code.

Saved as plain JSON under exports/ (alongside the other runtime state this
app already keeps there, like the phone-scanner's cached certificate) -
deliberately NOT inside data/, so it's obvious this is connection settings
for this one PC, not part of the shop's actual data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import db as db_module

_CONFIG_PATH = db_module.ensure_output_dir() / "till_config.json"

_DEFAULT = {"mode": "standalone", "server_host": None, "server_port": None, "pairing_code": None}


def load() -> dict:
    if not _CONFIG_PATH.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(_CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return dict(_DEFAULT)
    merged = dict(_DEFAULT)
    merged.update({k: v for k, v in data.items() if k in _DEFAULT})
    return merged


def save(mode: str, *, server_host: Optional[str] = None, server_port: Optional[int] = None,
          pairing_code: Optional[str] = None) -> None:
    if mode not in ("standalone", "server", "client"):
        raise ValueError(f"unknown till mode: {mode}")
    data = {"mode": mode, "server_host": server_host, "server_port": server_port, "pairing_code": pairing_code}
    _CONFIG_PATH.write_text(json.dumps(data, indent=2))
