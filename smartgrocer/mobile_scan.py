"""
SmartGrocer - phone-as-barcode-scanner companion.

Lets any phone on the same shop Wi-Fi become a wireless barcode/QR scanner
for the POS screen, with nothing to install: the phone opens a web page in
its own browser (Safari on iPhone, Chrome on Android - it makes no
difference, since this is a plain web page, not a native app). The camera
preview opens once and stays open - it scans automatically in the
background (no tapping needed), with a manual "Scan Now" button kept as a
deliberate backup for a code the auto-loop isn't catching.

Design notes, since a few choices here are deliberate rather than obvious:

- Camera preview stays open continuously (`getUserMedia`) and decodes a
  frame automatically roughly every half second - hands-free scanning:
  point the phone at the next item and it's added on its own, on the
  FIRST frame that reads successfully (see `_handle_scan_request` for why
  an earlier revision waited for two consecutive matching frames instead,
  and why that was removed - it made a working scanner feel unreliable
  far more often than it ever caught a genuine misread). The "Scan Now"
  button is still there as a deliberate, full-effort capture for a code
  that isn't decoding automatically at all (a small/glossy label, poor
  lighting) - useful to force one focused attempt rather than waiting on
  the loop, not to double-check a read the loop already made. Either path
  decodes a frame from the SAME still-open preview - nothing ever needs
  reopening between items. Requires HTTPS - browsers refuse
  camera access to a plain-HTTP page unless it's "localhost", and this
  page is loaded from the PC's LAN IP (e.g. https://192.168.1.5:8765).
  There's no real certificate authority for a private shop network to get
  a trusted certificate from, so this generates its own self-signed one
  (`_ensure_self_signed_cert`, cached in exports/ so it doesn't regenerate
  every launch). The phone's browser will show a "connection not private"
  warning the FIRST time it opens the page - that's expected for a
  self-signed certificate talking to a device on your own network, not a
  sign of anything wrong; the cashier taps through it once per phone (see
  the POS screen's dialog text for the exact steps) and it's remembered
  after that.
- Each captured frame posts one downscaled JPEG; the server decodes it
  and, on a successful read, adds the item and tells the phone what it
  added. The auto-loop keeps capturing every half second, so simply
  holding the phone steady on the item it just added would otherwise
  read - and add - the SAME code again and again for as long as it stays
  in frame. Rather than guarding that with a fixed cooldown (which either
  reopens too soon if the cashier lingers, or blocks a genuine "customer
  is buying two of these" re-scan for too long afterwards), the guard is
  state-based: the same code won't add again until the camera has seen
  SOMETHING else in between - a different code, or simply a frame with no
  barcode in it at all, which is exactly what naturally happens the
  moment the phone moves toward the next item. See `_seen_other_since_add`.
- The single biggest reason a clearly-printed barcode fails to read
  turned out to have nothing to do with focus or lighting: OpenCV's
  barcode detector only reliably reads a barcode whose bars land in a
  narrow absolute pixel-width range, and that range does NOT scale with
  the photo's resolution - it fails outright once the bars are wider than
  that, no matter how sharp or well-lit the shot is. Holding a product at
  the distance that feels natural for "getting a good close-up" very
  easily produces bars wider than that range, and the read just fails.
  `_decode_code` compensates by resizing the whole frame down across
  several different amounts before each decode attempt, since neither the
  phone nor the server can know in advance how close a given shot was
  taken from - shrinking a too-big barcode's bars back into the readable
  range recovers a read that would otherwise never succeed, regardless of
  image quality. This has two effort levels (the `thorough` flag): the
  live auto-loop (several times a second, no cashier action) uses
  `_SCAN_SCALES_FAST` - 4 shrink factors, grayscale only - because running
  the full search on every single frame while an item is still being
  lined up is what "takes a long time to read one item" actually turns
  out to mean (the loop falls behind its own tick rate, not the barcode
  failing to read); "Scan Now" and photo mode, each a single deliberate
  already-waited-for action, use `_SCAN_SCALES_FULL` (7 factors, plus the
  raw color frame at each) and, if still nothing, fall back to slower
  reprocessings of the full-size frame (`_heavy_variants`: local-contrast
  boost, a sharpening pass for a shot that's slightly out of focus, and a
  modest upscale for the rarer case of a barcode that's too small/far in
  frame).
- A phone's autofocus is still worth a nudge where the browser allows
  it: the camera is requested with an "ideal" HD resolution and, on
  browsers that support it (Chrome/Android; Safari/iOS does not expose
  this), a `focusMode: continuous` advanced constraint so the camera
  keeps hunting for focus instead of locking onto whatever it first saw.
  Tapping the video preview itself re-applies that constraint as a
  manual "nudge" for a camera whose autofocus has stalled.
- Plain HTTP-over-TLS request/response instead of a persistent connection
  (WebSocket). Each capture is already just one request/response round
  trip, so a standing connection would only add a second, hand-written
  protocol to get right for no real benefit. If the camera preview can't
  start at all (permission denied, or an older browser without
  `getUserMedia`), the page falls back to the phone's native camera app
  for a one-photo-per-item flow using the exact same `/scan` endpoint.
- Barcode/QR decoding tries pyzbar first (see the module-level comment
  above `_zbar_decode`) - it wraps the mature, purpose-built zbar C
  library, which reads a barcode reliably across a far wider range of
  sizes/distances/angles than a from-scratch approach can match. It's an
  OPTIONAL dependency (`pip install pyzbar` - on Windows this is fully
  self-contained, nothing extra to install) rather than a hard one: if
  it's missing, or its native library fails to load, decoding falls back
  to OpenCV (`cv2.barcode.BarcodeDetector` for EAN-13/EAN-8/UPC-A/UPC-E,
  `cv2.QRCodeDetector` for QR - already a project dependency's cousin,
  pure `pip install`), which needs the multiscale workaround `_decode_code`
  builds around it (see `_SCAN_SCALES_FULL`) to work around its own much
  narrower effective bar-width range. Either decoder reads standard
  printed barcodes and QR codes; a dedicated USB/Bluetooth scanner is
  still the faster, more forgiving choice for a busy till, but this makes
  a reliable, hands-free backup that needs no extra hardware - installing
  pyzbar is the single biggest thing that can be done to improve how
  reliably this reads a barcode.
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
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

import cv2
import numpy as np

from . import db as db_module

# pyzbar (wrapping the mature, purpose-built zbar C library) reads a
# barcode reliably across a far wider range of sizes/distances/angles
# than OpenCV's own cv2.barcode.BarcodeDetector does - see the long
# comment above _SCAN_SCALES_FULL for the specific narrow-bar-width
# limitation zbar simply doesn't have. It's an OPTIONAL dependency
# (`pip install pyzbar` - on Windows this is fully self-contained, no
# separate system library to install) rather than a hard one, so the app
# keeps working with just the OpenCV fallback below if it isn't
# installed; but installing it is by far the single biggest lever on
# real-world scan reliability available here. Guarded broadly (not just
# ImportError) because some pyzbar builds only try to load their bundled
# native library on the FIRST actual decode() call rather than at import
# time - see `_zbar_broken` below for the matching runtime guard.
try:
    from pyzbar.pyzbar import decode as _zbar_decode, ZBarSymbol as _ZBarSymbol
    _ZBAR_SYMBOLS = [
        _ZBarSymbol.EAN13, _ZBarSymbol.EAN8, _ZBarSymbol.UPCA, _ZBarSymbol.UPCE,
        _ZBarSymbol.CODE128, _ZBarSymbol.QRCODE,
    ]
except Exception:
    _zbar_decode = None
    _ZBAR_SYMBOLS = None

# Flips True the first time an actual _zbar_decode(...) call throws
# (rather than just being unavailable) - e.g. pyzbar imported fine but its
# bundled native library still failed to load on this particular PC. Once
# that happens there's no reason to keep re-attempting and re-failing it
# on every single frame; fall back to the OpenCV pipeline for the rest of
# this run instead.
_zbar_broken = False


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


# cv2's barcode detector turns out to only reliably read a barcode whose
# BAR WIDTH lands in a narrow absolute pixel range (measured directly:
# roughly 2-6px per narrow bar) - it fails outright once the bars get
# wider than that, no matter how sharp, well-lit, or high-resolution the
# photo is. That range doesn't scale with the photo's resolution, which
# means the deciding factor isn't image quality at all - it's simply how
# much of the FRAME the barcode fills. A product held at the distance
# that feels natural for "getting a good close-up shot" very easily
# produces bars far wider than that, and then no amount of sharpening or
# extra light saves the read. `_SCAN_SCALES_FULL`/`_SCAN_SCALES_FAST` compensate by resizing the
# WHOLE frame down before each decode attempt - shrinking a too-big
# barcode's bars back down into the range the detector can actually read
# - and trying several different amounts of shrinking, since neither the
# server nor the phone can know in advance how close a given shot was
# taken from.
_SCAN_SCALES_FULL = (1.0, 0.6, 0.4, 0.28, 0.2, 0.14, 0.1)

# The live auto-loop fires every LIVE_INTERVAL_MS (500ms in the page's JS)
# with no cashier action at all - it has to come back fast, because
# `busy` (see sgCaptureLiveFrame) blocks every later tick until the
# current one's request finishes. A slow decode here doesn't show up as
# "no barcode found" - it shows up as the WHOLE loop falling behind, i.e.
# exactly "takes a long time to read one item" even though the barcode
# itself was fine and in frame the whole time. Running the full 7-scale x
# 2-color-variant x 2-detector search (below) on every single live frame,
# most of which are just the cashier still lining the item up, was the
# actual cause: that's up to ~28 detectAndDecode calls before a single
# frame gives up, several times a second. `_SCAN_SCALES_FAST` and the
# `thorough` flag on `_decode_code` cut that down to ~4 calls for a live
# tick, while "Scan Now"/photo mode (a single, deliberate, already-waited-
# for action - the page shows "Decoding...") still get the full search.
_SCAN_SCALES_FAST = (1.0, 0.55, 0.3, 0.15)


def _try_barcode(candidate: np.ndarray, barcode_detector) -> Optional[str]:
    try:
        text, _points, _straight = barcode_detector.detectAndDecode(candidate)
        return text or None
    except cv2.error:
        return None


def _try_qr(candidate: np.ndarray, qr_detector) -> Optional[str]:
    try:
        text, _points, _ = qr_detector.detectAndDecode(candidate)
        return text or None
    except cv2.error:
        return None


def _heavy_variants(gray: np.ndarray):
    """Slower fallback reprocessings of a full-size grayscale frame, tried
    only once none of the (much cheaper) scaled/cheap attempts panned out.
    Each targets one specific way a shot can be "almost readable" rather
    than being a generic image-quality fix."""
    # CLAHE - local contrast boost, helps a dim or glare-washed shot.
    try:
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        yield clahe.apply(gray)
    except cv2.error:
        pass
    # Unsharp mask - claws back some edge definition from a shot that's
    # slightly out of focus rather than badly blurred.
    blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=3)
    yield cv2.addWeighted(gray, 1.5, blurred, -0.5, 0)
    # A modest upscale - for the (much rarer) opposite problem, a barcode
    # that's small/far away in frame; more pixels to work with sometimes
    # is all a too-small code needs.
    yield cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)


def _try_detect(candidate: np.ndarray, barcode_detector, qr_detector) -> Optional[str]:
    return _try_barcode(candidate, barcode_detector) or _try_qr(candidate, qr_detector)


def _try_zbar(gray: np.ndarray) -> Optional[str]:
    """One zbar decode attempt on an already-grayscale frame. Returns None
    (never raises) whether that's because zbar isn't installed, it just
    didn't find anything in this particular frame, or its native library
    turned out to be broken on this PC - see `_zbar_broken` above for why
    that last case disables further attempts for the rest of this run
    rather than re-raising on every subsequent frame."""
    global _zbar_broken
    if _zbar_decode is None or _zbar_broken:
        return None
    try:
        from PIL import Image
        results = _zbar_decode(Image.fromarray(gray), symbols=_ZBAR_SYMBOLS)
    except Exception:
        _zbar_broken = True
        return None
    if not results:
        return None
    try:
        return results[0].data.decode("utf-8")
    except Exception:
        return None


def _decode_code(frame: np.ndarray, barcode_detector, qr_detector, thorough: bool = False) -> Optional[str]:
    """Try zbar first (see `_try_zbar` and the module-level comment above
    `_zbar_decode`) - a couple of attempts there catch the large majority
    of real-world shots, since zbar doesn't share OpenCV's narrow
    effective bar-width range. Only when zbar isn't installed, or didn't
    find anything, does this fall back to the from-scratch OpenCV search:
    a standard printed barcode first (the common case for a manufactured
    product) across several shrink factors of the whole frame (see
    _SCAN_SCALES_FULL/_FAST) so a barcode that fills too much of the shot
    still lands in a bar-width cv2's detector can read, then QR (a
    membership code, say - rarer here, and not limited to that narrow bar-
    width, so one check at native size covers it without repeating at
    every shrink factor). `thorough=False` (the live auto-loop's speed) only
    tries grayscale at 4 shrink factors; `thorough=True` ("Scan Now"/photo
    mode - a single deliberate action worth spending more time on) also
    tries the raw color frame at each of 7 shrink factors, and - if still
    nothing - falls back to the slower _heavy_variants reprocessings of the
    full-size frame. Returns the decoded text, or None if nothing along the
    way read anything.

    (A revision of this briefly returned a (text, confident) pair and made
    the caller require an unconfident read to repeat on a second live frame
    before acting on it, to guard against an occasional wrong-but-checksum-
    valid misread on a small/far barcode. That made scanning feel
    noticeably slower/less responsive overall - reported directly - so it's
    reverted back to this simpler, single-value, accept-on-first-decode
    form; a stray misread is still guarded by the duplicate check in
    _handle_scan_request and by each symbology's own check digit.)"""
    gray_full = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    text = _try_zbar(gray_full)
    if text:
        return text
    # zbar already tolerates a barcode that's too BIG in frame far better
    # than cv2's detector does - the gap worth covering here is the
    # opposite case: a SMALL/far-away barcode, where a modest upscale
    # gives it more pixels to work with. Unlike the OpenCV multiscale
    # search below (deliberately skipped on the live loop - see
    # `_SCAN_SCALES_FAST` above - because it's what made "takes a long
    # time to read one item" happen), this is just one more zbar call on
    # an already-fast library, so it runs every time, live loop included -
    # a small barcode failing to read at all was reported as a live-loop-
    # specific complaint, and this is the cheap fix for exactly that,
    # without reintroducing the slow multiscale search into every tick.
    upscaled = cv2.resize(gray_full, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    text = _try_zbar(upscaled)
    if text:
        return text

    scales = _SCAN_SCALES_FULL if thorough else _SCAN_SCALES_FAST
    full_gray: Optional[np.ndarray] = None
    checked_qr = False
    for scale in scales:
        candidate_frame = frame if scale == 1.0 else cv2.resize(
            frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
        )
        gray = gray_full if scale == 1.0 else cv2.cvtColor(candidate_frame, cv2.COLOR_BGR2GRAY)
        if scale == 1.0:
            full_gray = gray

        text = _try_barcode(gray, barcode_detector)
        if text:
            return text
        if thorough:
            text = _try_barcode(candidate_frame, barcode_detector)
            if text:
                return text
        if thorough or not checked_qr:
            checked_qr = True
            text = _try_qr(gray, qr_detector)
            if text:
                return text
    if thorough and full_gray is not None:
        for candidate in _heavy_variants(full_gray):
            text = _try_detect(candidate, barcode_detector, qr_detector)
            if text:
                return text
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
        self._last_added_code: Optional[str] = None
        # Starts True (vacuously - nothing's been added yet, so there's
        # nothing to guard against re-adding) and flips to False the
        # instant something IS added; see _handle_scan_request for how it
        # flips back to True again (seeing anything other than that same
        # code) and why that's a better guard than a fixed time window.
        self._seen_other_since_add: bool = True
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

        # `live` (sent by the auto-loop, absent/false for "Scan Now" and
        # photo mode) picks the decode effort level - see _decode_code's
        # `thorough` parameter and the note above _SCAN_SCALES_FAST for why
        # the auto-loop needs the cheaper pass to avoid falling behind.
        is_live = bool(payload.get("live"))

        # Every way this can fail to produce a usable frame - bad base64,
        # an empty buffer (cv2.imdecode raises on that instead of
        # returning None like it does for other bad input), or a buffer
        # that just isn't a valid image - is folded into `decoded` staying
        # None rather than returning early, so it all reaches the SAME
        # "nothing found" handling below as a frame that decoded fine but
        # had no barcode in it. That matters: that shared branch is also
        # what tells the duplicate guard the camera saw something other
        # than the last item added (see _seen_other_since_add) - an early
        # return here would have skipped that reset for no reason tied to
        # what's actually in front of the camera.
        decoded = None
        image_field = payload.get("image", "")
        if "," in image_field:  # strip the "data:image/jpeg;base64," prefix if present
            image_field = image_field.split(",", 1)[1]
        try:
            raw = base64.b64decode(image_field, validate=True)
        except (binascii.Error, ValueError):
            raw = b""
        if raw:
            arr = np.frombuffer(raw, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                decoded = _decode_code(frame, self._barcode_detector, self._qr_detector, thorough=not is_live)

        with self._lock:
            if not decoded:
                # Nothing recognisable in this frame at all - that's the
                # camera having moved away from whatever it last saw
                # (toward the next item, or just off any barcode for a
                # moment), which is exactly the signal the duplicate guard
                # below is waiting for.
                self._seen_other_since_add = True
                return {"ok": True, "found": False}

            # A revision briefly required an unconfident read (see the note
            # on _decode_code) to repeat on a second live frame before
            # acting on it, as a guard against an occasional wrong-but-
            # checksum-valid misread on a small/far barcode. That made
            # scanning feel noticeably slower/less responsive overall -
            # reported directly - so this is back to accepting on the
            # first successful decode, live auto-scan included, same as
            # before that revision. A stray misread is still guarded by
            # the duplicate check just below (it would only ever double-
            # add if it happened to imagine two DIFFERENT wrong codes in a
            # row) and by the barcode's own built-in check digit (EAN/UPC
            # reject most corrupted reads outright, before even reaching
            # here).
            if decoded == self._last_added_code and not self._seen_other_since_add:
                # Same code as last time, and nothing else has been seen
                # since - the phone is still sitting on the item it just
                # added (or briefly lost and re-found the exact same
                # barcode), not being pointed at a fresh one. Don't add it
                # again; the cashier moving to a genuinely different item
                # (or just off the barcode for a frame) is what re-arms
                # this, however long that takes - there's no clock here to
                # race against, unlike the fixed cooldown this replaced.
                return {"ok": True, "found": True, "code": decoded, "duplicate": True}
            self._last_added_code = decoded
            self._seen_other_since_add = False

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
  <div class="sub">Point the camera at a barcode - it scans automatically. Tap Scan Now if it's not catching one.</div>

  <div id="screen-connect">
    <div class="card">
      <div style="margin-bottom:10px;">Enter the code shown on the till screen:</div>
      <input id="code-input" inputmode="numeric" maxlength="4" placeholder="0000" autocomplete="off">
      <button class="primary" onclick="sgConnect()">Start Scanning</button>
      <div id="connect-error" class="warn"></div>
    </div>
  </div>

  <div id="screen-scan">
    <div id="mode-badge" class="badge badge-live" style="margin-bottom:10px;">Starting camera&hellip;</div>
    <video id="video" autoplay playsinline muted></video>
    <div class="sub" style="margin:-6px 0 10px;">Tap the video if the picture looks blurry, to refocus.</div>
    <canvas id="canvas" style="display:none;"></canvas>
    <!-- A short beep, embedded as base64 so this page stays one self-
         contained file with nothing extra to fetch. Played via sgBeep()
         below - see sgEnsureAudio for why an <audio> element, "unlocked"
         by a real tap, is what actually gets a phone to make sound here,
         where a bare WebAudio oscillator alone was not reliable enough on
         every phone. -->
    <audio id="beep-audio" preload="auto"><source src="data:audio/wav;base64,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" type="audio/wav"></audio>

    <!-- Camera preview stays open the whole time and scans on its own in
         the background (sgCaptureLiveFrame, on a timer). This button is a
         deliberate backup for a code the auto-loop isn't catching - one
         full-effort capture of whatever's currently in frame, without
         ever closing/reopening the camera (sgCaptureAndScan). -->
    <button id="capture-btn" class="scan-btn" style="display:none;" onclick="sgCaptureAndScan()">&#128247; Scan Now</button>
    <!-- True fallback only: shown when the browser can't open a live
         camera preview at all, so each item is one photo via the
         phone's own separate Camera app instead (sgUsePhotoMode). -->
    <button id="photo-btn" class="scan-btn" style="display:none;"
            onclick="document.getElementById('camera-input').click()">Take Photo</button>
    <input id="camera-input" type="file" accept="image/*" capture="environment" style="display:none">

    <div id="status" class="sub">&nbsp;</div>
    <div class="card" id="log"></div>
  </div>

<script>
var pairingCode = sessionStorage.getItem('sg_code') || '';
var busy = false;

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
  sgEnsureAudio();  // a real tap - prime the beep sound now, see sgEnsureAudio's own comment
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

// A previous version of this beeped via a raw WebAudio oscillator, primed
// by creating/resuming one AudioContext from a real tap. That still
// wasn't reliably audible on every phone - some browsers (iOS Safari in
// particular, across versions) only reliably unlock playback through an
// actual <audio>/<video> ELEMENT, not a bare AudioContext, even one
// resumed during a genuine tap. This switches to that more broadly
// reliable trick instead: beep-audio (see the <audio> element above,
// holding a short embedded beep sound) gets play()'d once from an actual
// button tap (sgEnsureAudio, called from "Start Scanning" and "Scan Now"
// below) and immediately paused again right after - nothing is heard from
// that priming play, but the phone now remembers this element was played
// during a real gesture, and lets EVERY later play() call through, even
// one triggered from a background timer/network callback, which is what
// every actual scan-result beep is. sgEnsureAudio is safe to call many
// times - once unlocked, later calls are just an instant no-op.
var beepAudio = document.getElementById('beep-audio');
var audioUnlocked = false;

function sgEnsureAudio() {
  if (audioUnlocked || !beepAudio) return;
  try {
    var playResult = beepAudio.play();
    if (playResult && playResult.then) {
      playResult.then(function () {
        beepAudio.pause();
        beepAudio.currentTime = 0;
        audioUnlocked = true;
      }).catch(function () { /* still locked - the next tap tries again */ });
    } else {
      // Older browsers: play() doesn't return a promise at all, so there's
      // nothing to wait on - assume the tap was enough.
      beepAudio.pause();
      beepAudio.currentTime = 0;
      audioUnlocked = true;
    }
  } catch (e) { /* audio isn't essential - a silent scan still works */ }
}

function sgBeep() {
  try {
    if (!beepAudio) return;
    beepAudio.currentTime = 0;
    var playResult = beepAudio.play();
    if (playResult && playResult.catch) playResult.catch(function () {});
  } catch (e) { /* audio isn't essential - a silent scan still works */ }
}

// --- Camera preview opens once and stays open, scanning automatically in
// the background (sgCaptureLiveFrame, on a timer) - nothing to tap for the
// common case. sgCaptureAndScan (the "Scan Now" button) is a deliberate,
// full-effort backup for a code the auto-loop isn't catching. ---
var liveTimer = null;
// A successful decode almost always resolves in well under 200ms (it
// returns the moment any one shrink factor succeeds, often the very
// first) - the `busy` flag already stops overlapping requests, so a
// shorter tick just means the next attempt starts sooner after the last
// one actually finishes, instead of waiting out the rest of a fixed
// half-second for no reason.
var LIVE_INTERVAL_MS = 350;

function sgStartCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    sgUsePhotoMode('This browser can\\'t use the camera here - using one-photo-per-item mode instead.');
    return;
  }
  sgRequestCamera(false);
}

// Split out of sgStartCamera so the very first attempt can retry itself
// once before giving up to photo mode: on some Android browsers, the
// FIRST getUserMedia call right after a phone links up - especially right
// after tapping through the "connection not private" warning - can fail
// even though the camera itself is fine and permission is actually
// granted a split second later. Without this, that showed up as "link
// the phone the first time and it opens the old tap-a-photo scanner
// instead of the live camera, but linking it again right after works
// fine" - a real, if intermittent, browser-timing quirk, not a
// permission the cashier actually needs to fix by hand each time.
function sgRequestCamera(isRetry) {
  navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: 'environment',
      width: { ideal: 1280 },
      height: { ideal: 720 },
      // Non-standard/optional - browsers that don't support keeping the
      // camera hunting for focus (Safari/iOS, some Android WebViews)
      // simply ignore this rather than failing getUserMedia over it.
      advanced: [{ focusMode: 'continuous' }]
    }
  })
    .then(function (stream) {
      var video = document.getElementById('video');
      video.srcObject = stream;
      video.style.display = 'block';
      document.getElementById('mode-badge').textContent = 'Live scanning - point at a barcode';
      document.getElementById('mode-badge').className = 'badge badge-live';
      document.getElementById('photo-btn').style.display = 'none';
      document.getElementById('capture-btn').style.display = 'block';
      document.getElementById('status').textContent = 'Hold steady on the barcode - it adds itself automatically.';
      sgStartLiveLoop();
    })
    .catch(function () {
      if (!isRetry) {
        setTimeout(function () { sgRequestCamera(true); }, 700);
        return;
      }
      sgUsePhotoMode('Camera access wasn\\'t granted - using one-photo-per-item mode instead (tap Take Photo each time).');
    });
}

function sgStartLiveLoop() {
  if (liveTimer) return;
  liveTimer = setInterval(sgCaptureLiveFrame, LIVE_INTERVAL_MS);
}

function sgStopLiveLoop() {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
}

// Pausing the auto-loop while the phone's screen is off/backgrounded
// avoids burning battery and Wi-Fi on frames nobody's looking at; it
// picks back up as soon as the page is visible again.
document.addEventListener('visibilitychange', function () {
  if (document.hidden) {
    sgStopLiveLoop();
  } else if (pairingCode && document.getElementById('video').srcObject) {
    sgStartLiveLoop();
  }
});

// Re-applying the focusMode constraint is a legitimate way to ask the
// camera to re-hunt for focus on some Android/Chrome versions - used both
// as a manual "tap to nudge a stalled autofocus" and automatically right
// after an item is added (see sgHandleResult): the camera was just
// settled on THIS item's distance, and the cashier is about to move to
// the next one at a different distance, so kicking off the re-hunt the
// moment we know that's about to happen (instead of waiting for it to
// notice on its own, or for the cashier to remember to tap) is what
// keeps the very next item from feeling slow to catch. Wrapped
// defensively since most phones (notably Safari/iOS) don't support this
// constraint at all; on those it's simply a harmless no-op.
function sgNudgeFocus() {
  try {
    var video = document.getElementById('video');
    var track = video.srcObject && video.srcObject.getVideoTracks()[0];
    if (track && track.applyConstraints) {
      track.applyConstraints({ advanced: [{ focusMode: 'continuous' }] }).catch(function () {});
    }
  } catch (e) { /* not supported on this phone - nothing to do */ }
}
document.getElementById('video').addEventListener('click', sgNudgeFocus);

function sgUsePhotoMode(message) {
  document.getElementById('video').style.display = 'none';
  document.getElementById('capture-btn').style.display = 'none';
  document.getElementById('mode-badge').textContent = 'Photo mode';
  document.getElementById('mode-badge').className = 'badge badge-photo';
  document.getElementById('photo-btn').style.display = 'block';
  document.getElementById('status').textContent = message;
}

function sgCaptureFrame(targetWidth, quality) {
  var video = document.getElementById('video');
  if (!video.videoWidth) return null;  // stream not ready yet
  var canvas = document.getElementById('canvas');
  var scale = targetWidth / video.videoWidth;
  canvas.width = targetWidth;
  canvas.height = video.videoHeight * scale;
  var ctx2d = canvas.getContext('2d');
  ctx2d.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg', quality);
}

// Shared by every capture path below - posts either an already-decoded
// value (native on-device detection - the common case now) or a raw
// image (the compatibility fallback) and routes the result the same way
// either way. A hard timeout on top of the request, not just a network
// error to catch: `busy` gates every future tick until THIS one settles,
// so a request that never resolves at all (a phone that briefly drops
// off Wi-Fi mid-request, rather than failing outright) would otherwise
// freeze the auto-loop permanently until the page is reloaded - "it
// scanned fine, then just stopped" with no obvious cause. Aborting after
// a few seconds guarantees busy always gets released again.
function sgSubmitScan(extra, opts) {
  opts = opts || {};
  var controller = new AbortController();
  var timedOut = false;
  var timeoutId = setTimeout(function () { timedOut = true; controller.abort(); }, 4000);
  var body = { code: pairingCode, live: !!opts.live };
  for (var k in extra) { body[k] = extra[k]; }
  fetch('/scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(function (r) { return r.json(); })
    .then(function (r) { clearTimeout(timeoutId); busy = false; sgHandleResult(r, opts); })
    .catch(function () {
      clearTimeout(timeoutId);
      busy = false;
      if (opts.live) {
        // A dropped or timed-out tick in a continuous loop isn't worth
        // alarming about on its own - the next tick tries again - but a
        // hung request is worth a quiet, non-scary status note rather
        // than silence that looks like the scanner just died.
        if (timedOut) {
          document.getElementById('status').textContent = 'Connection to the till is slow right now - still watching.';
        }
      } else {
        document.getElementById('status').textContent = 'Could not reach the till - check the Wi-Fi.';
      }
    });
}

// Captures a frame and lets the server's OpenCV pipeline decode it (see
// _SCAN_SCALES_FAST / the `thorough` flag on _decode_code in
// mobile_scan.py for that path's own speed tuning) - every phone works
// the same way through here, nothing device-dependent to fall back from.
// 900px (was 720) - a small/far-away barcode only has so many pixels to
// begin with, and shrinking the shot down further before it's even sent
// was throwing away detail that mattered specifically for that case; 900
// is still comfortably inside what a local Wi-Fi round trip every
// LIVE_INTERVAL_MS can carry without the loop falling behind.
function sgCaptureLiveFrame() {
  if (busy) return;
  busy = true;
  var dataUrl = sgCaptureFrame(900, 0.7);
  if (!dataUrl) { busy = false; return; }
  sgSubmitScan({ image: dataUrl }, { live: true });
}

// "Scan Now": one deliberate, full-effort grab of whatever the
// still-open camera preview is currently pointed at, decoded on its own
// - a bigger, higher-quality frame than the auto-loop uses since there's
// only ever one of these in flight at a time. The stream itself is never
// stopped or restarted, so the preview is already ready for the very
// next scan (automatic or manual) the moment this one's result is back.
function sgCaptureAndScan() {
  if (busy) return;
  busy = true;
  sgEnsureAudio();  // a real tap too - no-op once already unlocked, see sgEnsureAudio's own comment
  document.getElementById('status').textContent = 'Decoding...';
  var dataUrl = sgCaptureFrame(1024, 0.85);
  if (!dataUrl) { busy = false; return; }
  sgSubmitScan({ image: dataUrl }, {});
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

function sgHandleResult(r, opts) {
  opts = opts || {};
  var status = document.getElementById('status');
  if (!r.ok && r.error === 'wrong_pairing_code') {
    pairingCode = '';
    sessionStorage.removeItem('sg_code');
    sgStopLiveLoop();
    document.getElementById('connect-error').textContent = 'That code was rejected - check the till and re-enter it.';
    sgShowScreen();
    return;
  }
  if (!r.found) {
    // In live mode most ticks won't see (or won't yet have confirmed) a
    // barcode - that's completely normal between items, so stay quiet
    // rather than flashing "not found" every half second. A manual
    // "Scan Now" tap is a deliberate one-shot, so it still gets a
    // straight answer either way.
    if (!opts.live) {
      status.textContent = 'No barcode found in that shot - line it up and try again, or tap the video to refocus.';
    }
    return;
  }
  if (r.duplicate) {
    status.textContent = 'Already added - point at the next item.';
    return;
  }
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
  sgNudgeFocus();  // get the camera re-hunting focus now, ahead of the next item
  // Deliberate one-second pause before the NEXT capture is allowed to go
  // out - long enough to actually hear the beep and see the log line
  // update before the camera starts trying to read anything else. Reusing
  // `busy` (the same flag that already stops two captures overlapping
  // while one is in flight) rather than a separate flag: it's already
  // checked at the top of both sgCaptureLiveFrame (the automatic loop)
  // and sgCaptureAndScan ("Scan Now"), so holding it true for a little
  // longer than usual pauses either path the same way, with nothing extra
  // to wire up.
  busy = true;
  status.textContent = 'Added \\u2713 - next scan in a moment...';
  setTimeout(function () {
    busy = false;
    if (status.textContent.indexOf('next scan in a moment') !== -1) {
      status.textContent = '\\u00a0';
    }
  }, 1000);
}

sgShowScreen();
</script>
</body>
</html>
"""
