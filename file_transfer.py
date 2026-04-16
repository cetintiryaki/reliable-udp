"""
file_transfer.py - File transfer (images, videos, any binary) over reliable UDP

Usage:
  # Receiver side (start first):
  python file_transfer.py receive <port> <output_file>

  # Sender side:
  python file_transfer.py send <host> <port> <input_file> [loss_rate]

  # Self-contained loopback demo:
  python file_transfer.py demo <file> [loss_rate]

Examples:
  python file_transfer.py receive 9000 received.mp4
  python file_transfer.py send 127.0.0.1 9000 video.mp4 0.02

  python file_transfer.py demo photo.jpg 0.05
"""

import sys
import time
import os
import threading
import struct

from sender   import ReliableSender
from receiver import ReliableReceiver
from packet   import MAX_SEGMENT_SIZE


# ── MIME detection ───────────────────────────────────────────────────────────

MEDIA_SIGNATURES = {
    b"\xff\xd8\xff"         : "JPEG image",
    b"\x89PNG\r\n\x1a\n"   : "PNG image",
    b"\x47\x49\x46\x38"    : "GIF image",
    b"RIFF"                 : "WAV/AVI media",
    b"\x00\x00\x00\x18ftyp": "MP4 video",
    b"\x00\x00\x00\x1cftyp": "MP4 video",
    b"ID3"                  : "MP3 audio",
    b"OggS"                 : "OGG media",
    b"BM"                   : "BMP image",
    b"\x1aE\xdf\xa3"       : "MKV/WebM video",
    b"PK\x03\x04"          : "ZIP archive",
}

def detect_type(data: bytes) -> str:
    for sig, name in MEDIA_SIGNATURES.items():
        if data[:len(sig)] == sig:
            return name
    return "binary/unknown"


# ── Progress bar ─────────────────────────────────────────────────────────────

class ProgressBar:
    def __init__(self, total_bytes: int, label: str = "", width: int = 40):
        self.total   = total_bytes
        self.label   = label
        self.width   = width
        self.sent    = 0
        self.start   = time.time()
        self._lock   = threading.Lock()

    def update(self, n_bytes: int) -> None:
        with self._lock:
            self.sent += n_bytes
            self._draw()

    def finish(self) -> None:
        with self._lock:
            self.sent = self.total
            self._draw()
            print()   # newline after bar

    def _draw(self) -> None:
        if self.total == 0:
            return
        frac     = min(self.sent / self.total, 1.0)
        filled   = int(self.width * frac)
        bar      = "█" * filled + "░" * (self.width - filled)
        pct      = frac * 100
        elapsed  = time.time() - self.start
        speed    = (self.sent / 1024 / elapsed) if elapsed > 0 else 0
        size_str = _fmt_bytes(self.sent) + " / " + _fmt_bytes(self.total)
        print(f"\r{self.label} [{bar}] {pct:5.1f}%  {size_str}  {speed:.0f} KB/s",
              end="", flush=True)


def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


# ── Sender with progress ──────────────────────────────────────────────────────

def run_sender(
    host: str,
    port: int,
    input_path: str,
    loss_rate: float = 0.0,
) -> None:
    if not os.path.exists(input_path):
        print(f"[TX] Error: file not found: {input_path}")
        sys.exit(1)

    with open(input_path, "rb") as f:
        data = f.read()

    file_type = detect_type(data)
    filename  = os.path.basename(input_path)
    print(f"[TX] File     : {filename} ({_fmt_bytes(len(data))}) — {file_type}")
    print(f"[TX] Dest     : {host}:{port}")
    print(f"[TX] Loss sim : {loss_rate * 100:.0f}%")
    print()

    # Prepend a small metadata header: filename length (2B) + filename bytes
    fname_bytes = filename.encode("utf-8")
    meta        = struct.pack("!H", len(fname_bytes)) + fname_bytes
    payload     = meta + data

    tx = ReliableSender(host, port, loss_rate=loss_rate, verbose=False)
    if not tx.connect():
        print("[TX] Could not connect to receiver")
        sys.exit(1)

    bar   = ProgressBar(len(payload), label="Sending")
    start = time.time()

    # Patch sender to call progress bar
    original_send_raw = tx._send_raw
    def tracked_send(pkt):
        original_send_raw(pkt)
        if pkt.data:
            bar.update(len(pkt.data))
    tx._send_raw = tracked_send

    tx.send(payload)
    bar.finish()

    elapsed    = time.time() - start
    throughput = len(payload) / 1024 / elapsed if elapsed > 0 else 0

    print(f"\n[TX] Done in {elapsed:.2f}s  —  avg {throughput:.0f} KB/s")
    print(f"[TX] Congestion events (last 5):")
    for e in tx.cc.log()[-5:]:
        print(f"     {e}")

    tx.close()


# ── Receiver with progress ────────────────────────────────────────────────────

def run_receiver(port: int, output_dir: str = ".") -> None:
    os.makedirs(output_dir, exist_ok=True)
    recv = ReliableReceiver("0.0.0.0", port, verbose=False)
    print(f"[RX] Listening on port {port}  (output dir: {output_dir})")
    print(f"[RX] Waiting for connection...\n")

    chunks: list[bytes] = []
    bar: list[ProgressBar] = []   # mutable container so callback can set it

    def on_chunk(chunk: bytes) -> None:
        chunks.append(chunk)
        if bar:
            bar[0].update(len(chunk))

    start = time.time()
    recv.listen(on_data=on_chunk)
    full = b"".join(chunks)

    if bar:
        bar[0].finish()

    # Parse metadata header
    if len(full) >= 2:
        fname_len = struct.unpack("!H", full[:2])[0]
        filename  = full[2: 2 + fname_len].decode("utf-8", errors="replace")
        data      = full[2 + fname_len:]
    else:
        filename = "received_file"
        data     = full

    out_path   = os.path.join(output_dir, filename)
    file_type  = detect_type(data)
    elapsed    = time.time() - start
    throughput = len(data) / 1024 / elapsed if elapsed > 0 else 0

    with open(out_path, "wb") as f:
        f.write(data)

    print(f"\n[RX] Received : {filename} ({_fmt_bytes(len(data))}) — {file_type}")
    print(f"[RX] Saved to : {out_path}")
    print(f"[RX] Time     : {elapsed:.2f}s  —  avg {throughput:.0f} KB/s")
    recv.close()


# ── Loopback demo ─────────────────────────────────────────────────────────────

def demo_loopback(file_path: str, loss_rate: float = 0.05) -> None:
    PORT    = 19000
    OUT_DIR = "/tmp/rdt_demo_output"

    rx_thread = threading.Thread(
        target=run_receiver, args=(PORT, OUT_DIR), daemon=True
    )
    rx_thread.start()
    time.sleep(0.3)

    run_sender("127.0.0.1", PORT, file_path, loss_rate)
    rx_thread.join(timeout=30)

    # Integrity check
    with open(file_path, "rb") as f:
        original = f.read()

    filename = os.path.basename(file_path)
    out_path = os.path.join(OUT_DIR, filename)
    if os.path.exists(out_path):
        with open(out_path, "rb") as f:
            received = f.read()
        if original == received:
            print(f"\n✓ Integrity check PASSED — files are identical")
        else:
            print(f"\n✗ Integrity check FAILED — {len(original)} vs {len(received)} bytes")
    else:
        print(f"\n✗ Output file not found: {out_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    mode = sys.argv[1]

    if mode == "receive":
        port     = int(sys.argv[2])
        out_dir  = sys.argv[3] if len(sys.argv) > 3 else "."
        run_receiver(port, out_dir)

    elif mode == "send":
        host    = sys.argv[2]
        port    = int(sys.argv[3])
        path    = sys.argv[4]
        loss    = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
        run_sender(host, port, path, loss)

    elif mode == "demo":
        path    = sys.argv[2] if len(sys.argv) > 2 else __file__
        loss    = float(sys.argv[3]) if len(sys.argv) > 3 else 0.05
        demo_loopback(path, loss)

    else:
        print(f"Unknown mode: {mode}")
        print(__doc__)
        sys.exit(1)
