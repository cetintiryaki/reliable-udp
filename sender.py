"""
sender.py - Reliable sender over UDP

Features:
  - Go-Back-N / Selective-Repeat hybrid sliding window
  - Timeout-based retransmission
  - Congestion control (slow start, CA, fast retransmit)
  - Flow control (respects receiver RWND)
  - Simulated packet loss (for testing)
"""

import socket
import threading
import time
import random
import logging
from typing import Optional

from packet import (Packet, DATA, ACK, SYN, FIN, SYNACK,
                    MAX_SEGMENT_SIZE, TIMEOUT)
from congestion import CongestionController

logger = logging.getLogger("sender")


class ReliableSender:
    def __init__(
        self,
        dest_host: str,
        dest_port: int,
        loss_rate: float = 0.0,    # simulated packet loss [0.0 – 1.0]
        verbose: bool = True,
    ):
        self.dest       = (dest_host, dest_port)
        self.loss_rate  = loss_rate
        self.verbose    = verbose

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(TIMEOUT)

        self.cc   = CongestionController()
        self.rwnd = 16            # updated from receiver's advertised window

        # sliding window state
        self.base     = 0         # oldest unACKed seq
        self.next_seq = 0         # next seq to send
        self.window: dict[int, tuple[Packet, float]] = {}   # seq → (pkt, send_time)
        self.lock     = threading.Lock()

        self._running  = False
        self._ack_thread: Optional[threading.Thread] = None

    # ── public API ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        """Three-way handshake (SYN → SYNACK → ACK)."""
        syn = Packet(SYN, seq_num=0)
        for attempt in range(5):
            self._send_raw(syn)
            try:
                raw, _ = self.sock.recvfrom(2048)
                pkt = Packet.from_bytes(raw)
                if pkt and pkt.ptype == SYNACK:
                    ack = Packet(ACK, seq_num=1, ack_num=pkt.seq_num + 1)
                    self._send_raw(ack)
                    self._log(f"Connected (attempt {attempt+1})")
                    return True
            except socket.timeout:
                self._log(f"SYN timeout, retrying ({attempt+1}/5)...")
        logger.error("Connection failed after 5 attempts")
        return False

    def send(self, data: bytes) -> int:
        """Send arbitrary bytes reliably. Returns bytes sent."""
        segments = [data[i: i + MAX_SEGMENT_SIZE]
                    for i in range(0, len(data), MAX_SEGMENT_SIZE)]
        total = len(segments)
        self._log(f"Sending {len(data)} bytes in {total} segments")

        self._running = True
        self._ack_thread = threading.Thread(target=self._recv_acks, daemon=True)
        self._ack_thread.start()

        idx = 0
        while self.base < total:
            with self.lock:
                effective_win = min(int(self.cc.window), self.rwnd)
                while idx < total and self.next_seq < self.base + effective_win:
                    pkt = Packet(DATA, seq_num=self.next_seq, data=segments[idx])
                    self._send_pkt(pkt)
                    idx += 1
                    self.next_seq += 1

            # Check for timeouts
            now = time.time()
            with self.lock:
                for seq, (pkt, t_sent) in list(self.window.items()):
                    if now - t_sent > TIMEOUT:
                        self._log(f"Timeout on seq={seq}, retransmitting")
                        self.cc.on_timeout()
                        self._send_pkt(pkt)

            time.sleep(0.01)

        # Send FIN
        fin = Packet(FIN, seq_num=self.next_seq)
        self._send_raw(fin)
        self._log("FIN sent")

        self._running = False
        return len(data)

    def close(self) -> None:
        self._running = False
        self.sock.close()

    # ── internals ─────────────────────────────────────────────────────────────

    def _recv_acks(self) -> None:
        prev_ack = -1
        dup_count = 0
        while self._running:
            try:
                raw, _ = self.sock.recvfrom(2048)
                pkt = Packet.from_bytes(raw)
                if not pkt or pkt.ptype != ACK:
                    continue

                ack = pkt.ack_num
                self.rwnd = max(1, pkt.win_size)

                with self.lock:
                    if ack > self.base:
                        is_dup = False
                        # Slide window
                        for seq in list(self.window.keys()):
                            if seq < ack:
                                del self.window[seq]
                        self.base = ack
                        prev_ack  = ack
                        dup_count = 0
                    else:
                        is_dup = True
                        dup_count += 1

                self.cc.on_ack(is_duplicate=is_dup)

                if dup_count == 3:
                    # Fast retransmit: resend self.base
                    with self.lock:
                        if self.base in self.window:
                            lost_pkt, _ = self.window[self.base]
                            self._log(f"Fast retransmit seq={self.base}")
                            self._send_pkt(lost_pkt)

            except socket.timeout:
                pass

    def _send_pkt(self, pkt: Packet) -> None:
        """Send with optional simulated loss; track in window."""
        self.window[pkt.seq_num] = (pkt, time.time())
        self._send_raw(pkt)

    def _send_raw(self, pkt: Packet) -> None:
        if random.random() < self.loss_rate:
            self._log(f"[SIM DROP] {pkt}")
            return
        self.sock.sendto(pkt.to_bytes(), self.dest)
        if self.verbose:
            self._log(f"→ {pkt}")

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[SENDER] {msg}")
