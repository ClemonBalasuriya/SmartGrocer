"""
SmartGrocer - phone-as-barcode-scanner companion.

Lets any phone on the same shop Wi-Fi become a wireless barcode/QR scanner
for the POS screen, with nothing to install: the phone just opens a web
page in its own browser (Safari on iPhone, Chrome on Android - it makes no
difference, since this is a plain web page, not a native app) and takes a
photo of the code with its normal camera app. SmartGrocer decodes the
photo and adds the item to the cart, then tells the phone what it added.

Design notes, since a few choices here are deliberate rather than obvious:

- No live camera preview / continuous auto-scan. The phone uses the
  standard HTML `<input type=file capture>` control, which hands the
  photo-taking off to the phone's own native camera app. The alternative
  (a live in-page video feed via `getUserMedia`) needs the page to be
  loaded over HTTPS - browsers refuse camera access to a plain-HTTP page
  unless it's "localhost", and this page is loaded from this PC's LAN IP
  (e.g. http://192.168.1.5:8765) so it doesn't qualify. Serving HTTPS here
  would mean generating a self-signed certificate and asking every phone
  to click through a "not private" warning the first time - a confusing,
  fragile ask for a cashier. One tap per item to open the camera is a
  little slower than true live scanning, but it works everywhere,
  reliably, with no security prompts to explain.
- Plain HTTP request/response instead of a persistent connection
  (WebSocket). Every scan is already a full round trip anyway (send the
  photo, get back what was decoded and added), so a standing connection
  wouldn't make it feel any more instant - it would only add a second,
  hand-written network protocol to get right and keep working. A plain
  POST to a local Python HTTP server is about as close to "cannot break"
  as networking code gets.
- Barcode/QR decoding happens with OpenCV (`cv2.barcode.BarcodeDetector`
  for EAN-13/EAN-8/UPC-A/UPC-E, `cv2.QRCodeDetector` for QR) - already a
  project dependency's cousin (numpy), pure `pip install`, no separate
  system library (like zbar) to install by hand on Windows. It reads
  standard printed barcodes and QR codes; a dedicated USB/Bluetooth
  scanner is still the faster, more forgiving choice for a busy till, but
  this makes a reliable backup that needs no extra hardware.
- A short pairing code (shown on the POS screen, entered once on the
  phone) guards the /scan endpoint - the server is reachable by anything
  on the shop's Wi-Fi, so without this, anyone on that network could add
  items to a stranger's cart.
"""

from __future__ import annotations

import base64
import binascii
import json
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

import cv2
import numpy as np


def get_lan_ip() -> str:
    """Best-effort LAN address for this PC - what a phone on the same
    Wi-Fi needs to type/scan to reach it. Doesn't actually send traffic;
    just asks the OS which local interface it would use to reach the
    internet, which is normally the Wi-Fi/Ethernet adapter a phone on the
    same network can also reach."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def generate_qr_png_bytes(data: str, scale: int = 8, border: int = 32) -> bytes:
    """Render `data` as a QR code PNG, using only OpenCV (no external
    barcode library available in this environment) - lets the POS screen
    show a QR code the phone's own camera app can scan to jump straight to
    the scanning page, instead of the cashier typing a URL by hand."""
    encoder = cv2.QRCodeEncoder.create()
    matrix = encoder.encode(data)  # small 2D array of 0/255 modules, no quiet zone
    big = cv2.resize(matrix, (matrix.shape[1] * scale, matrix.shape[0] * scale),
                      interpolation=cv2.INTER_NEAREST)
    padded = cv2.copyMakeBorder(big, border, border, border, border,
                                 cv2.BORDER_CONSTANT, value=255)
    ok, buf = cv2.imencode(".png", padded)
    if not ok:
        raise RuntimeError("failed to encode QR PNG")
    return buf.tobytes()


def _decode_code(frame: np.ndarray, barcode_detector, qr_detector) -> Optional[str]:
    """Try a standard printed barcode first (the common case for a
    manufactured product), then QR. Returns the decoded text, or None."""
    try:
        text, _points, _straight = barcode_detector.detectAndDecode(frame)
        if text:
            return text
    except cv2.error:
        pass
    try:
        text, _points, _ = qr_detector.detectAndDecode(frame)
        if text:
            return text
    except cv2.error:
        pass
    return None


class MobileScanServer:
    """Owns a background HTTP server that serves the phone-scanning page
    and receives decoded scans. `on_scan(code)` is called from the
    server's own request-handling thread (never the Tkinter thread) with
    the decoded barcode/QR text, and must return a dict describing what
    happened - see POSScreen.handle_phone_scan for the shape expected."""

    def __init__(self, on_scan: Callable[[str], dict]):
        self.on_scan = on_scan
        self.pairing_code = f"{secrets.randbelow(10000):04d}"
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._barcode_detector = cv2.barcode.BarcodeDetector()
        self._qr_detector = cv2.QRCodeDetector()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else 0

    @property
    def url(self) -> str:
        return f"http://{get_lan_ip()}:{self.port}/"

    def start(self, port: int = 0) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # this fires once per photo taken - keep the console quiet

            def _send_json(self, status: int, payload: dict):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    body = _PAGE_HTML.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path != "/scan":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, TypeError):
                    self._send_json(400, {"ok": False, "error": "bad_request"})
                    return
                self._send_json(200, server._handle_scan_request(payload))

        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
            self._thread = None

    def _handle_scan_request(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or payload.get("code") != self.pairing_code:
            return {"ok": False, "error": "wrong_pairing_code"}

        image_field = payload.get("image", "")
        if "," in image_field:  # strip the "data:image/jpeg;base64," prefix if present
            image_field = image_field.split(",", 1)[1]
        try:
            raw = base64.b64decode(image_field, validate=True)
        except (binascii.Error, ValueError):
            return {"ok": True, "found": False}

        arr = np.frombuffer(raw, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return {"ok": True, "found": False}

        decoded = _decode_code(frame, self._barcode_detector, self._qr_detector)
        if not decoded:
            return {"ok": True, "found": False}

        try:
            result = self.on_scan(decoded) or {}
        except Exception as e:  # a bug here shouldn't take the whole server down
            return {"ok": True, "found": True, "code": decoded, "error": str(e)}
        return {"ok": True, "found": True, "code": decoded, **result}


_PAGE_HTML = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>SmartGrocer Scanner</title>
<style>
  body { font-family: -apple-system, "Segoe UI", Roboto, sans-serif; background:#0B1730; color:#fff;
         margin:0; padding:20px; min-height:100vh; box-sizing:border-box; }
  h1 { font-size:19px; margin:0 0 4px; }
  .sub { color:#9FB0CC; font-size:13px; margin-bottom:18px; }
  input { width:100%; box-sizing:border-box; padding:14px; font-size:24px; text-align:center;
          letter-spacing:6px; border-radius:10px; border:none; margin-bottom:12px; }
  button { width:100%; box-sizing:border-box; padding:18px; font-size:17px; font-weight:600;
           border:none; border-radius:10px; margin-bottom:10px; }
  .primary { background:#2F6FED; color:#fff; }
  .scan-btn { background:#1FAA59; color:#fff; font-size:22px; padding:28px; }
  .card { background:#16223F; border-radius:12px; padding:16px; margin-bottom:12px; }
  .log-item { padding:10px 0; border-bottom:1px solid #22304F; font-size:14px; }
  .ok { color:#6EE7A8; }
  .warn { color:#F4A6A6; }
  #screen-connect, #screen-scan { display:none; }
</style>
</head>
<body>
  <h1>SmartGrocer &ndash; Phone Scanner</h1>
  <div class="sub">Photograph a barcode or QR code and it's added straight to the till's cart.</div>

  <div id="screen-connect">
    <div class="card">
      <div style="margin-bottom:10px;">Enter the code shown on the till screen:</div>
      <input id="code-input" inputmode="numeric" maxlength="4" placeholder="0000" autocomplete="off">
      <button class="primary" onclick="sgConnect()">Start Scanning</button>
      <div id="connect-error" class="warn"></div>
    </div>
  </div>

  <div id="screen-scan">
    <button class="scan-btn" onclick="document.getElementById('camera-input').click()">Scan Item</button>
    <input id="camera-input" type="file" accept="image/*" capture="environment" style="display:none">
    <div id="status" class="sub">&nbsp;</div>
    <div class="card" id="log"></div>
  </div>

<script>
var pairingCode = sessionStorage.getItem('sg_code') || '';

function sgShowScreen() {
  var connected = !!pairingCode;
  document.getElementById('screen-connect').style.display = connected ? 'none' : 'block';
  document.getElementById('screen-scan').style.display = connected ? 'block' : 'none';
}

function sgConnect() {
  var v = document.getElementById('code-input').value.trim();
  if (v.length !== 4) {
    document.getElementById('connect-error').textContent = 'Enter the 4-digit code shown on the till.';
    return;
  }
  pairingCode = v;
  sessionStorage.setItem('sg_code', v);
  document.getElementById('connect-error').textContent = '';
  sgShowScreen();
}

function sgAddLog(text, ok) {
  var log = document.getElementById('log');
  var div = document.createElement('div');
  div.className = 'log-item ' + (ok ? 'ok' : 'warn');
  div.textContent = text;
  log.insertBefore(div, log.firstChild);
}

document.getElementById('camera-input').addEventListener('change', function (e) {
  var file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  var status = document.getElementById('status');
  status.textContent = 'Decoding...';
  var reader = new FileReader();
  reader.onload = function () {
    fetch('/scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: pairingCode, image: reader.result })
    })
      .then(function (r) { return r.json(); })
      .then(sgHandleResult)
      .catch(function () { status.textContent = 'Could not reach the till - check the Wi-Fi.'; });
  };
  reader.readAsDataURL(file);
});

function sgHandleResult(r) {
  var status = document.getElementById('status');
  status.textContent = '\\u00a0';
  if (!r.ok && r.error === 'wrong_pairing_code') {
    pairingCode = '';
    sessionStorage.removeItem('sg_code');
    document.getElementById('connect-error').textContent = 'That code was rejected - check the till and re-enter it.';
    sgShowScreen();
    return;
  }
  if (!r.found) {
    status.textContent = 'No barcode found in that photo - try again, closer and in good light.';
    return;
  }
  if (r.error) {
    sgAddLog('Scanned ' + r.code + ' - ' + r.error, false);
    return;
  }
  if (r.unknown) {
    sgAddLog('Scanned ' + r.code + ' - not in the catalogue', false);
    return;
  }
  sgAddLog('Added: ' + r.name + ' (' + r.code + ')', true);
}

sgShowScreen();
</script>
</body>
</html>
"""
