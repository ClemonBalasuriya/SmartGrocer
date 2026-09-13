"""
SmartGrocer - notifications for sensitive staff/account actions.

The idea from the shop's owner: when an Admin changes something sensitive
(resets a cashier's PIN, adds or removes staff), the Owner should hear
about it immediately, by message - not just be able to look it up later in
the Activity Log screen (audit.py covers that side).

Two genuinely different channels, and it's worth being upfront about the
difference:

- Email works right away, at no extra cost, with an account you probably
  already have - it's just SMTP. Fill in the Owner's Gmail address and an
  "app password" (Google Account -> Security -> App Passwords - NOT your
  normal Gmail password) in the Activity Log screen's Notification
  Settings, tick "enable email", and it starts sending.
- SMS and WhatsApp are NOT free and NOT keyless for anyone - there is no
  provider that sends either without a paid account and a phone number to
  send from. The most common choice for a small business is Twilio
  (https://www.twilio.com/console), which offers both an SMS-capable
  number and a WhatsApp Business sender (their sandbox number works for
  testing before you're approved for your own). The Twilio sending code
  below is real and complete - it's a plain HTTPS POST with HTTP Basic
  Auth, no SDK needed - and starts working the moment real
  twilio_account_sid/twilio_auth_token/twilio_from_sms (or
  twilio_from_whatsapp) values are filled in; until then it's simply never
  called. This hasn't been tested against a real Twilio account (there
  isn't one to test against here) - the first real message is worth
  checking arrives before relying on it.

send() never raises - a notification failing to go out must never be
allowed to block the action that triggered it (a PIN reset has to still
succeed even if the Owner's inbox rejects the email).
"""

from __future__ import annotations

import base64
import json
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from email.mime.text import MIMEText
from pathlib import Path

from . import db as db_module

_CONFIG_PATH = db_module.ensure_output_dir() / "notify_config.json"

_DEFAULT = {
    "email_enabled": False,
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "",            # the Gmail address that SENDS the notification
    "smtp_app_password": "",    # a Gmail "app password" - not the normal account password
    "owner_email": "",          # who receives every notification
    # Twilio (SMS/WhatsApp) - leave blank to keep both disabled. See the
    # module docstring above for what's needed to turn these on.
    "twilio_account_sid": "",
    "twilio_auth_token": "",
    "twilio_from_sms": "",          # your Twilio phone number, e.g. "+14155551234"
    "twilio_from_whatsapp": "",     # e.g. "whatsapp:+14155238886" (Twilio's own sandbox sender)
    "owner_phone": "",              # where SMS/WhatsApp notifications go, e.g. "+94771234567"
}


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


def save(config: dict) -> None:
    merged = dict(_DEFAULT)
    merged.update({k: v for k, v in config.items() if k in _DEFAULT})
    _CONFIG_PATH.write_text(json.dumps(merged, indent=2))


def send(subject: str, message: str) -> list[str]:
    """Best-effort fan-out to every channel that's actually configured.
    Returns the list of channels that reported success (e.g. ["email"]) -
    useful for a "test notification" button, but the caller should not
    treat an empty list as an error worth surfacing to whoever triggered
    the underlying action."""
    config = load()
    sent = []

    if config["email_enabled"] and config["smtp_user"] and config["smtp_app_password"] and config["owner_email"]:
        try:
            _send_email(config, subject, message)
            sent.append("email")
        except Exception:
            pass

    if config["twilio_account_sid"] and config["twilio_auth_token"] and config["owner_phone"]:
        if config["twilio_from_sms"]:
            try:
                _send_twilio(config, config["twilio_from_sms"], config["owner_phone"], message)
                sent.append("sms")
            except Exception:
                pass
        if config["twilio_from_whatsapp"]:
            try:
                _send_twilio(config, config["twilio_from_whatsapp"], f"whatsapp:{config['owner_phone']}", message)
                sent.append("whatsapp")
            except Exception:
                pass

    return sent


def _send_email(config: dict, subject: str, message: str) -> None:
    msg = MIMEText(message)
    msg["Subject"] = subject
    msg["From"] = config["smtp_user"]
    msg["To"] = config["owner_email"]
    with smtplib.SMTP(config["smtp_host"], int(config["smtp_port"]), timeout=10) as server:
        server.starttls()
        server.login(config["smtp_user"], config["smtp_app_password"])
        server.sendmail(config["smtp_user"], [config["owner_email"]], msg.as_string())


def _send_twilio(config: dict, from_: str, to: str, message: str) -> None:
    url = f"https://api.twilio.com/2010-04-01/Accounts/{config['twilio_account_sid']}/Messages.json"
    data = urllib.parse.urlencode({"From": from_, "To": to, "Body": message}).encode()
    auth = base64.b64encode(f"{config['twilio_account_sid']}:{config['twilio_auth_token']}".encode()).decode()
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Basic {auth}"})
    urllib.request.urlopen(req, timeout=10)
