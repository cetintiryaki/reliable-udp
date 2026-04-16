"""
chat.py - Terminal chat demo using the reliable UDP protocol

Two peers connect to each other and exchange messages reliably over UDP.

Usage:
  # Start peer A (listens on 9001, sends to peer B at 9002):
  python chat.py 9001 127.0.0.1 9002

  # Start peer B (listens on 9002, sends to peer A at 9001):
  python chat.py 9002 127.0.0.1 9001
"""

import sys
import socket
import threading
import time

from packet import Packet, DATA, ACK, SYN, SYNACK, FIN, MAX_SEGMENT_SIZE, TIMEOUT


class ChatPeer:
    def __init__(self, local_port: int, remote_host: str, remote_port: int):
        self.local_addr  = ("0.0.0.0", local_port)
        self.remote_addr = (remote_host, remote_port)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(self.local_addr)
        self.sock.settimeout(0.3)

        self.seq_out  = 0
        self.seq_in   = 0
        self._running = True

    # ── public ───────────────────────────────────────────────────────────────

    def run(self) -> None:
        print(f"[CHAT] Listening on port {self.local_addr[1]}, "
              f"sending to {self.remote_addr[0]}:{self.remote_addr[1]}")
        print("[CHAT] Type a message and press Enter. Ctrl+C to quit.\n")

        recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        recv_thread.start()

        try:
            while self._running:
                try:
                    msg = input()
                except EOFError:
                    break

                if not msg:
                    continue
                if msg.lower() in ("/quit", "/exit"):
                    break

                self._send_message(msg)

        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            self.sock.close()
            print("\n[CHAT] Disconnected.")

    # ── internals ─────────────────────────────────────────────────────────────

    def _send_message(self, text: str) -> None:
        """Send a message with simple stop-and-wait (enough for chat)."""
        data  = text.encode("utf-8")
        chunk = data[:MAX_SEGMENT_SIZE]
        pkt   = Packet(DATA, seq_num=self.seq_out, data=chunk)

        for _ in range(10):   # up to 10 retransmissions
            self.sock.sendto(pkt.to_bytes(), self.remote_addr)
            try:
                raw, _ = self.sock.recvfrom(2048)
                ack = Packet.from_bytes(raw)
                if ack and ack.ptype == ACK and ack.ack_num == self.seq_out + 1:
                    self.seq_out += 1
                    return
            except socket.timeout:
                pass  # retransmit

        print("[CHAT] Warning: message may not have been delivered")

    def _recv_loop(self) -> None:
        while self._running:
            try:
                raw, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break

            pkt = Packet.from_bytes(raw)
            if not pkt:
                continue

            if pkt.ptype == DATA:
                if pkt.seq_num == self.seq_in:
                    msg = pkt.data.decode("utf-8", errors="replace")
                    print(f"\n[{addr[0]}:{addr[1]}] {msg}")
                    print("", end="", flush=True)
                    self.seq_in += 1

                # Always ACK (cumulative)
                ack = Packet(ACK, ack_num=self.seq_in)
                self.sock.sendto(ack.to_bytes(), addr)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    local_port  = int(sys.argv[1])
    remote_host = sys.argv[2]
    remote_port = int(sys.argv[3])

    peer = ChatPeer(local_port, remote_host, remote_port)
    peer.run()
