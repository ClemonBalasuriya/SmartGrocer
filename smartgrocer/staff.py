"""
SmartGrocer - staff (owner/admin/cashier) management.

Staff rows are never deleted, only deactivated - invoices.staff_id references
them, so removing a row would either break that reference or silently orphan
historical sales figures against a name nobody can see anymore. Deactivating
just hides someone from the login list once they've left, while every past
invoice/cash session they rang up still shows their name in reports.

Three roles, each a superset of the one before: 'cashier' can ring up
sales; 'admin' can additionally add staff, add/edit catalog items, and
activate/deactivate staff; 'owner' can do everything admin can, plus
remove a catalog item and view the Activity Log (audit.py) of every
sensitive action any admin has taken.

There is deliberately exactly ONE 'owner' account, ever, for the whole
shop - db.py seeds it automatically (PIN 1234, changeable via "Change My
PIN") so it always exists from the very first run, add_staff below refuses
to create a second one (enforced here, not just left to the GUI, since
this is the one role invariant worth protecting at the data layer too),
and set_active refuses to deactivate it - there is never a moment with no
Owner at all, and never ambiguity about which account IS the Owner.

Nobody - not even the Owner - can set or see another account's PIN
directly anymore (an earlier version let an Admin/Owner force-reset
someone else's PIN, which is exactly the kind of thing a compromised
Admin/Owner account could abuse, so it was removed). Every account -
cashier, admin, or owner alike - can only change their OWN PIN, either
directly (knowing their current one) or, if they've forgotten it, via a
random PIN emailed/texted to the contact info on file (see
generate_temp_pin below and gui/app.py's "Change My PIN" / "Forgot PIN?"
dialogs) - neither of those is role-gated, since both only ever touch the
logged-in (or PIN-recovering) person's own account. An Admin/Owner can
still update_contact() someone else's saved email/phone (e.g. to give a
person who wasn't set up with one a way to recover their own PIN later) -
that's the one indirect lever left, and it still never reveals or sets
their PIN itself.

PINs are stored hashed (see _hash_pin/_pin_matches), never as plain text -
add_staff and update_pin below both hash before writing, and callers
(gui/app.py's Login and "Change My PIN" dialogs) check a typed PIN with
verify_pin rather than comparing the pin column directly. This matters
because a real shop's database file (a stolen laptop, a leaked backup, a
copy handed to someone for support) used to hand over every staff
member's actual login PIN just by opening it in a text/SQLite viewer -
hashing means that file alone no longer does that.

A database that already existed before this changed (this shop's own
production database included) still has some/all PINs sitting in the pin
column as plain text (e.g. a seeded default like '1234', or an old
add_staff/update_pin call from before hashing existed) - verify_pin
recognises and accepts that legacy plaintext form too, and transparently
rehashes it the moment it's confirmed correct, so every account migrates
itself to the hashed form on its own next successful login, one account
at a time, with no separate migration script, no forced PIN reset, and no
downtime. There is nothing else to run to get an existing shop's database
onto the new format - it happens automatically as staff log in normally.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

_PIN_HASH_PREFIX = "pbkdf2_sha256"
_PIN_HASH_ITERATIONS = 260_000  # a Django/NIST-era-appropriate work factor for pbkdf2-sha256


def _hash_pin(pin: str) -> str:
    """Turn a plain PIN into its stored form:
    "pbkdf2_sha256$<iterations>$<salt-hex>$<hash-hex>". A 4-6 digit numeric
    PIN has very little entropy on its own (trying every "0000".."9999"
    takes no time at all) - hashing it doesn't change that, but it does
    mean a copy of the database file alone no longer hands over every
    staff member's actual PIN in plain text; whoever has the file still has
    to run that same tiny brute-force per account rather than just reading
    pin='1234' off the row directly. Uses hashlib's stdlib pbkdf2_hmac (no
    new entry needed in requirements.txt) with a random per-account salt,
    so two staff who happen to pick the same PIN don't end up with the
    same stored value either."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), bytes.fromhex(salt), _PIN_HASH_ITERATIONS)
    return f"{_PIN_HASH_PREFIX}${_PIN_HASH_ITERATIONS}${salt}${digest.hex()}"


def _pin_matches(stored: str, candidate: str) -> bool:
    """True if `candidate` (whatever was just typed) is the PIN
    represented by `stored` (whatever's actually in the pin column) -
    whichever of the two formats `stored` happens to be in. Has to accept
    EITHER a real "pbkdf2_sha256$..." stored value (the normal case,
    checked by recomputing the hash with the embedded salt/iteration
    count) OR a bare legacy plaintext PIN (checked directly), since a
    database that predates PIN hashing still has old accounts sitting in
    the latter form until their next successful login migrates them (see
    verify_pin below) - this can't assume every row is already hashed.
    Both comparisons use a constant-time compare (hmac.compare_digest) so
    neither path leaks how much of the PIN was right via response timing."""
    if stored.startswith(_PIN_HASH_PREFIX + "$"):
        try:
            _prefix, iterations_str, salt, hash_hex = stored.split("$", 3)
            iterations = int(iterations_str)
        except ValueError:
            return False  # corrupted stored value - fail closed, never fall through to a plaintext compare
        digest = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), bytes.fromhex(salt), iterations)
        return hmac.compare_digest(digest.hex(), hash_hex)
    return hmac.compare_digest(stored, candidate)  # legacy plaintext row


def verify_pin(conn: sqlite3.Connection, staff_id: int, candidate: str) -> bool:
    """Check `candidate` against staff_id's real PIN - this is what
    gui/app.py's Login and "Change My PIN" dialogs call instead of
    comparing the pin column directly. The one thing this does beyond a
    plain yes/no: the moment a legacy PLAINTEXT PIN is confirmed correct,
    it's rehashed into the salted pbkdf2 form right here, so an existing
    shop's database migrates itself one account at a time, on that
    account's own next successful login, with no separate migration step.
    A WRONG PIN never triggers this - only a confirmed-correct one, since
    rehashing on a failed attempt would tell an attacker nothing useful
    but would still be an extra, pointless database write on every
    mistyped PIN."""
    row = conn.execute("SELECT pin FROM staff WHERE id=?", (staff_id,)).fetchone()
    if row is None:
        return False
    stored = row["pin"]
    if not _pin_matches(stored, candidate):
        return False
    if not stored.startswith(_PIN_HASH_PREFIX + "$"):
        conn.execute("UPDATE staff SET pin=? WHERE id=?", (_hash_pin(candidate), staff_id))
        conn.commit()
    return True


def list_staff(conn: sqlite3.Connection, active_only: bool = False) -> list[sqlite3.Row]:
    query = "SELECT * FROM staff"
    if active_only:
        query += " WHERE active=1"
    query += " ORDER BY active DESC, name"
    return conn.execute(query).fetchall()


def add_staff(
    conn: sqlite3.Connection,
    name: str,
    role: str,
    pin: str,
    email: str | None = None,
    phone: str | None = None,
) -> int:
    """email/phone are optional and only used so notifications.py can message
    this person directly (e.g. "your PIN was changed") rather than only the
    shop Owner - leave either blank if this person doesn't need that."""
    if role not in ("owner", "admin", "cashier"):
        raise ValueError("role must be 'owner', 'admin', or 'cashier'")
    if role == "owner" and conn.execute("SELECT 1 FROM staff WHERE role='owner' LIMIT 1").fetchone():
        raise ValueError("An Owner account already exists - only one Owner is allowed")
    if not name or not name.strip():
        raise ValueError("Name cannot be blank")
    if not pin or not pin.strip():
        raise ValueError("PIN cannot be blank")
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    if email is not None and "@" not in email:
        raise ValueError("Email address doesn't look valid")
    cur = conn.execute(
        "INSERT INTO staff (name, role, pin, active, email, phone) VALUES (?,?,?,1,?,?)",
        (name.strip(), role, _hash_pin(pin.strip()), email, phone),
    )
    conn.commit()
    return cur.lastrowid


def update_pin(conn: sqlite3.Connection, staff_id: int, new_pin: str) -> None:
    if not new_pin or not new_pin.strip():
        raise ValueError("PIN cannot be blank")
    conn.execute("UPDATE staff SET pin=? WHERE id=?", (_hash_pin(new_pin.strip()), staff_id))
    conn.commit()


def update_contact(conn: sqlite3.Connection, staff_id: int, email: str | None, phone: str | None) -> None:
    """Update the email/phone saved for a staff member - lets an Admin/Owner
    give someone a way to recover their own PIN later (via "Forgot PIN?")
    if they weren't set up with contact info when added. Never touches the
    PIN itself - nobody sets or sees another account's PIN, by design (see
    the module docstring)."""
    email = (email or "").strip() or None
    phone = (phone or "").strip() or None
    if email is not None and "@" not in email:
        raise ValueError("Email address doesn't look valid")
    conn.execute("UPDATE staff SET email=?, phone=? WHERE id=?", (email, phone, staff_id))
    conn.commit()


def set_active(conn: sqlite3.Connection, staff_id: int, active: bool) -> None:
    if not active:
        row = conn.execute("SELECT role FROM staff WHERE id=?", (staff_id,)).fetchone()
        if row and row["role"] == "owner":
            raise ValueError("The Owner account cannot be deactivated")
    conn.execute("UPDATE staff SET active=? WHERE id=?", (1 if active else 0, staff_id))
    conn.commit()


def generate_temp_pin(length: int = 6) -> str:
    """A random numeric PIN for the "forgot PIN" email-reset flow - long
    enough (6 digits, drawn with secrets rather than random so it isn't
    guessable) that it's impractical to guess before the real owner reads
    their email, without being awkward to type in at a till."""
    return "".join(secrets.choice("0123456789") for _ in range(length))
