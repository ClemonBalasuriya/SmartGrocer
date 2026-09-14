"""
SmartGrocer - phone-as-barcode-scanner companion.

Lets any phone on the same shop Wi-Fi become a wireless barcode/QR scanner
for the POS screen, with nothing to install: the phone opens a web page in
its own browser (Safari on iPhone, Chrome on Android - it makes no
difference, since this is a plain web page, not a native app), points its
camera at items, and each one lands in the till's cart within a second or
two, hands-free - no tapping a shutter button per item.

Design notes, since a few choices here are deliberate rather than obvious:

- Live camera feed, over HTTPS with a self-signed certificate. A
  continuous auto-scan view (`getUserMedia`) needs the page to be loaded
  over HTTPS - browsers refuse camera access to a plain-HTTP page unless
  it's "localhost", and this page is loaded from the PC's LAN IP (e.g.
  https://192.168.1.5:8765). There's no real certificate authority for a
  private shop network to get a trusted certificate from, so this
  generates its own self-signed one (`_ensure_self_signed_cert`, cached in
  exports/ so it doesn't regenerate every launch). The phone's browser
  will show a "connection not private" warning the FIRST time it opens the
  page - that's expected for a self-signed certificate talking to a
  device on your own network, not a sign of anything wrong; the cashier
  taps through it once per phone (see the POS screen's dialog text for the
  exact steps) and it's remembered after that.
- The phone posts a small downscaled JPEG frame roughly twice a second
  while the camera view is open; the server decodes it and, on a
  successful read, adds the item and tells the phone what it added. A
  short per-code cooldown (`_DUPLICATE_WINDOW_SECONDS`) stops the same
  item being added repeatedly while the phone is still pointed at it.
- Plain HTTP-over-TLS request/response instead of a persistent connection
  (WebSocket) for each frame. A standing connection wouldn't make this
  feel any more instant - each frame is already a full round trip (send
  it, get back what was decoded) - and would only add a second,
  hand-written protocol to get right. If the live view can't start (camera
  permission denied, or an older browser without `getUserMedia`), the page
  falls back to the phone's native camera app for a one-photo-per-item
  flow using the exact same `/scan` endpoint.
- Barcode/QR decoding happens with OpenCV (`cv2.barcode.BarcodeDetector`
  for EAN-13/EAN-8/UPC-A/UPC-E, `cv2.QRCodeDetector` for QR) - already a
  project dependency's cousin (numpy), pure `pip install`, no separate
  system library (like zbar) to install by hand on Windows. It reads
  standard printed barcodes and QR codes; a dedicated USB/Bluetooth
  scanner is still the faster, more forgiving choice for a busy till, but
  this makes a reliable, hands-free backup that needs no extra hardware.
- A short pairing code (shown on the POS screen, entered once on the
  phone) guards the /scan endpoint - the server is reachable by anything
  on the shop's Wi-Fi, so without this, anyone on that network could add
  items to a stranger's cart.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import ipaddress
import json
import secrets
import socket
import ssl
import tempfile
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

import cv2
import numpy as np

from . import db as db_module

_DUPLICATE_WINDOW_SECONDS = 2.5  # how long a just-scanned code is ignored, to stop one item adding itself repeatedly


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


def _ensure_self_signed_cert(lan_ip: str) -> tuple[Path, Path]:
    """Return (cert_path, key_path) for an HTTPS certificate covering this
    PC's current LAN IP, generating and caching one under exports/ if none
    exists yet or the cached one doesn't cover this IP (e.g. the PC picked
    up a new address from the router) or has expired. Reusing a cached
    cert across app restarts means a phone that already clicked through
    the "not private" warning once doesn't have to do it again every time
    the till app is relaunched."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_dir = db_module.ensure_output_dir()
    cert_path = cert_dir / "mobile_scan_cert.pem"
    key_path = cert_dir / "mobile_scan_key.pem"

    if cert_path.exists() and key_path.exists():
        try:
            existing = x509.load_pem_x509_certificate(cert_path.read_bytes())
            san = existing.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            covers_ip = ipaddress.ip_address(lan_ip) in san.get_values_for_type(x509.IPAddress)
            not_expired = existing.not_valid_after_utc > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
            if covers_ip and not_expired:
                return cert_path, key_path
        except Exception:
            pass  # anything wrong with the cached cert - just regenerate below

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SmartGrocer Phone Scanner")])
    now = datetime.datetime.now(datetime.timezone.utc)
    san_entries = [x509.IPAddress(ipaddress.ip_address(lan_ip)), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                   x509.DNSName("localhost")]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    return cert_path, key_path


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
    """Owns a background HTTPS server that serves the phone-scanning page
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
        self._last_code: Optional[str] = None
        self._last_code_at: float = 0.0
        self._pending_code: Optional[str] = None
        self._pending_count: int = 0
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._httpd is not None

    @property
    def port(self) -> int:
        return self._httpd.server_address[1] if self._httpd else 0

    @property
    def url(self) -> str:
        return f"https://{get_lan_ip()}:{self.port}/"

    def start(self, port: int = 0) -> None:
        if self._httpd is not None:
            return
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # this fires a couple of times a second while the camera view is open - keep the console quiet

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

            def handle_one_request(self):
                try:
                    super().handle_one_request()
                except (ssl.SSLError, ConnectionResetError, OSError):
                    pass  # a phone's browser dropping/retrying the connection isn't a server bug

        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        lan_ip = get_lan_ip()
        cert_path, key_path = _ensure_self_signed_cert(lan_ip)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
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

        # Only the continuous live-camera loop (sgCaptureLiveFrame, posting
        # every ~400ms) sets "live": true. A one-shot capture - the photo
        # fallback for phones/browsers without live camera access, a test,
        # or any other caller that only ever sends ONE frame for a given
        # scan - has no second frame to confirm against, so it must be
        # accepted on the first (and only) decode, exactly as before.
        is_live = bool(payload.get("live"))

        with self._lock:
            now = time.time()
            if decoded == self._last_code and (now - self._last_code_at) < _DUPLICATE_WINDOW_SECONDS:
                return {"ok": True, "found": True, "code": decoded, "duplicate": True}

            if is_live:
                # Require the SAME decoded text on two frames in a row
                # before accepting it, rather than acting on the very first
                # decode. A barcode read is very reliable across two
                # consecutive reads, but any ONE frame (motion blur, glare,
                # the barcode half out of frame) can occasionally decode to
                # a different, wrong string for the same physical barcode -
                # without this, that looked like scanning one item and
                # having two different products land in the cart. Frames
                # arrive ~2/second, so this costs at most a fraction of a
                # second before a genuinely new item is accepted, and
                # resets immediately if a different code shows up (so it
                # never gets "stuck" waiting to confirm a misread that
                # never repeats).
                if decoded != self._pending_code:
                    self._pending_code = decoded
                    self._pending_count = 1
                    return {"ok": True, "found": False}
                self._pending_count += 1
                if self._pending_count < 2:
                    return {"ok": True, "found": False}
                self._pending_code = None
                self._pending_count = 0

            self._last_code = decoded
            self._last_code_at = now

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
  button { width:100%; box-sizing:border-box; padding:16px; font-size:16px; font-weight:600;
           border:none; border-radius:10px; margin-bottom:10px; }
  .primary { background:#2F6FED; color:#fff; }
  .scan-btn { background:#1FAA59; color:#fff; font-size:20px; padding:24px; }
  .card { background:#16223F; border-radius:12px; padding:16px; margin-bottom:12px; }
  .log-item { padding:10px 0; border-bottom:1px solid #22304F; font-size:14px; }
  .ok { color:#6EE7A8; }
  .warn { color:#F4A6A6; }
  #screen-connect, #screen-scan { display:none; }
  #video { width:100%; border-radius:12px; background:#000; margin-bottom:10px; }
  .badge { display:inline-block; padding:3px 10px; border-radius:20px; font-size:11px; font-weight:600; }
  .badge-live { background:#1FAA59; color:#fff; }
  .badge-photo { background:#3A4B78; color:#fff; }
</style>
</head>
<body>
  <h1>SmartGrocer &ndash; Phone Scanner</h1>
  <div class="sub">Point the camera at barcodes/QR codes - each one is added straight to the till's cart.</div>

  <div id="screen-connect">
    <div class="card">
      <div style="margin-bottom:10px;">Enter the code shown on the till screen:</div>
      <input id="code-input" inputmode="numeric" maxlength="4" placeholder="0000" autocomplete="off">
      <button class="primary" onclick="sgConnect()">Start Scanning</button>
      <div id="connect-error" class="warn"></div>
    </div>
  </div>

  <div id="screen-scan">
    <div id="mode-badge" class="badge badge-live" style="margin-bottom:10px;">Live camera</div>
    <video id="video" autoplay playsinline muted></video>
    <canvas id="canvas" style="display:none;"></canvas>

    <button id="photo-btn" class="scan-btn" style="display:none;"
            onclick="document.getElementById('camera-input').click()">Scan Item</button>
    <input id="camera-input" type="file" accept="image/*" capture="environment" style="display:none">

    <div id="status" class="sub">&nbsp;</div>
    <div class="card" id="log"></div>
  </div>

<script>
var pairingCode = sessionStorage.getItem('sg_code') || '';
var busy = false;
var liveTimer = null;

function sgShowScreen() {
  var connected = !!pairingCode;
  document.getElementById('screen-connect').style.display = connected ? 'none' : 'block';
  document.getElementById('screen-scan').style.display = connected ? 'block' : 'none';
  if (connected) sgStartCamera();
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

function sgBeep() {
  try {
    var AudioCtx = window.AudioContext || window.webkitAudioContext;
    var ctx = new AudioCtx();
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.value = 0.15;
    osc.connect(gain); gain.connect(ctx.destination);
    osc.start();
    setTimeout(function () { osc.stop(); ctx.close(); }, 90);
  } catch (e) { /* audio isn't essential - a silent scan still works */ }
}

// --- Live camera mode: grab a frame roughly every 400ms and post it. ---
function sgStartCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    sgUsePhotoMode('This browser can\\'t use the live camera here - using one-photo-per-item mode instead.');
    return;
  }
  navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
    .then(function (stream) {
      var video = document.getElementById('video');
      video.srcObject = stream;
      document.getElementById('mode-badge').textContent = 'Live camera';
      document.getElementById('mode-badge').className = 'badge badge-live';
      document.getElementById('photo-btn').style.display = 'none';
      video.style.display = 'block';
      if (liveTimer) clearInterval(liveTimer);
      liveTimer = setInterval(sgCaptureLiveFrame, 400);
    })
    .catch(function () {
      sgUsePhotoMode('Camera access wasn\\'t granted - using one-photo-per-item mode instead (tap Scan Item each time).');
    });
}

function sgUsePhotoMode(message) {
  document.getElementById('video').style.display = 'none';
  document.getElementById('mode-badge').textContent = 'Photo mode';
  document.getElementById('mode-badge').className = 'badge badge-photo';
  document.getElementById('photo-btn').style.display = 'block';
  document.getElementById('status').textContent = message;
}

function sgCaptureLiveFrame() {
  if (busy) return;
  var video = document.getElementById('video');
  if (!video.videoWidth) return;  // stream not ready yet
  var canvas = document.getElementById('canvas');
  var targetWidth = 480;
  var scale = targetWidth / video.videoWidth;
  canvas.width = targetWidth;
  canvas.height = video.videoHeight * scale;
  var ctx2d = canvas.getContext('2d');
  ctx2d.drawImage(video, 0, 0, canvas.width, canvas.height);
  var dataUrl = canvas.toDataURL('image/jpeg', 0.6);
  busy = true;
  fetch('/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code: pairingCode, image: dataUrl, live: true })
  })
    .then(function (r) { return r.json(); })
    .then(function (r) { busy = false; sgHandleResult(r); })
    .catch(function () { busy = false; });
}

// --- Fallback: one photo per item via the phone's native camera app. ---
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
  if (!r.ok && r.error === 'wrong_pairing_code') {
    pairingCode = '';
    sessionStorage.removeItem('sg_code');
    document.getElementById('connect-error').textContent = 'That code was rejected - check the till and re-enter it.';
    sgShowScreen();
    return;
  }
  if (!r.found) {
    status.textContent = '\\u00a0';
    return;
  }
  if (r.duplicate) return;  // still pointed at the same item - stay quiet, don't spam the log
  status.textContent = '\\u00a0';
  if (r.error) {
    sgAddLog('Scanned ' + r.code + ' - ' + r.error, false);
    return;
  }
  if (r.unknown) {
    sgAddLog('Scanned ' + r.code + ' - not in the catalogue', false);
    return;
  }
  sgBeep();
  sgAddLog('Added: ' + r.name + ' (' + r.code + ')', true);
}

sgShowScreen();
</script>
</body>
</html>
"""
