"""
receiver.py - Reliable receiver over UDP

Features:
  - Cumulative ACKs with out-of-order buffering
  - Receiver-side flow control (advertised RWND)
  - Simulated packet loss (for testing)
"""

import socket
import random
import logging
from typing import Optional, Callable

from packet import (Packet, DATA, ACK, SYN, FIN, SYNACK,
                    MAX_SEGMENT_SIZE, MAX_WINDOW_SIZE)

logger = logging.getLogger("receiver")


class ReliableReceiver:
    def __init__(
        self,
        host: str,
        port: int,
        loss_rate: float = 0.0,
        verbose: bool = True,
    ):
        self.addr      = (host, port)
        self.loss_rate = loss_rate
        self.verbose   = verbose

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.addr)
        self._log(f"Listening on {host}:{port}")

        self.expected_seq = 0
        self.recv_buffer: dict[int, bytes] = {}    # out-of-order buffer
        self.rwnd = MAX_WINDOW_SIZE                # advertised window

        self._client_addr: Optional[tuple] = None
        self._data_chunks: list[bytes] = []

    # ── public API ────────────────────────────────────────────────────────────

    def listen(self, on_data: Optional[Callable[[bytes], None]] = None) -> bytes:
        """
        Block until a full transfer is received.
        Calls on_data(chunk) for each in-order chunk if provided.
        Returns the complete received bytes.
        """
        self._data_chunks = []
        self.expected_seq = 0
        self.recv_buffer  = {}

        # Wait for SYN
        self._handshake()

        while True:
            try:
                raw, addr = self.sock.recvfrom(MAX_SEGMENT_SIZE + 64)
            except socket.timeout:
                continue

            if random.random() < self.loss_rate:
                self._log(f"[SIM DROP] from {addr}")
                continue

            pkt = Packet.from_bytes(raw)
            if pkt is None:
                self._log("Corrupted packet, dropping")
                continue

            self._log(f"← {pkt}")

            if pkt.ptype == FIN:
                self._log("FIN received, transfer complete")
                ack = Packet(ACK, ack_num=self.expected_seq,
                             win_size=self.rwnd)
                self._send(ack, addr)
                break

            if pkt.ptype == DATA:
                self._handle_data(pkt, addr, on_data)

        return b"".join(self._data_chunks)

    def close(self) -> None:
        self.sock.close()

    # ── internals ─────────────────────────────────────────────────────────────

    def _handshake(self) -> None:
        """Wait for SYN, respond with SYNACK."""
        self._log("Waiting for SYN...")
        while True:
            try:
                raw, addr = self.sock.recvfrom(2048)
                pkt = Packet.from_bytes(raw)
                if pkt and pkt.ptype == SYN:
                    self._client_addr = addr
                    synack = Packet(SYNACK, seq_num=0, ack_num=1,
                                    win_size=self.rwnd)
                    self._send(synack, addr)
                    self._log(f"Handshake complete with {addr}")

                    # Wait for final ACK
                    raw2, _ = self.sock.recvfrom(2048)
                    # (ignore content, just consume it)
                    return
            except socket.timeout:
                pass

    def _handle_data(
        self,
        pkt: Packet,
        addr: tuple,
        on_data: Optional[Callable[[bytes], None]],
    ) -> None:
        seq = pkt.seq_num

        if seq == self.expected_seq:
            # In-order: deliver immediately
            self._deliver(pkt.data, on_data)
            self.expected_seq += 1

            # Deliver any buffered in-order packets
            while self.expected_seq in self.recv_buffer:
                chunk = self.recv_buffer.pop(self.expected_seq)
                self._deliver(chunk, on_data)
                self.expected_seq += 1

        elif seq > self.expected_seq:
            # Out-of-order: buffer it
            self._log(f"Out-of-order seq={seq}, buffering (expected {self.expected_seq})")
            self.recv_buffer[seq] = pkt.data

        # Always ACK up to expected_seq (cumulative)
        self.rwnd = max(1, MAX_WINDOW_SIZE - len(self.recv_buffer))
        ack = Packet(ACK, ack_num=self.expected_seq, win_size=self.rwnd)
        self._send(ack, addr)

    def _deliver(self, data: bytes, on_data: Optional[Callable]) -> None:
        self._data_chunks.append(data)
        if on_data:
            on_data(data)

    def _send(self, pkt: Packet, addr: tuple) -> None:
        self.sock.sendto(pkt.to_bytes(), addr)
        self._log(f"→ {pkt}")

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[RECEIVER] {msg}")
