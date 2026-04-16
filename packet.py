"""
packet.py - Packet structure and serialization for RDT (Reliable Data Transfer over UDP)

Packet format (16-byte header):
  [0]    type     : uint8   (DATA=0, ACK=1, SYN=2, FIN=3, SYNACK=4)
  [1]    flags    : uint8   (reserved)
  [2:4]  checksum : uint16
  [4:8]  seq_num  : uint32
  [8:12] ack_num  : uint32
  [12:14] win_size: uint16  (receiver advertised window, in packets)
  [14:16] data_len: uint16
  [16:]  data     : bytes
"""

import struct
import zlib

HEADER_FORMAT = "!BBHIIHHh"  # network byte order
HEADER_SIZE   = struct.calcsize(HEADER_FORMAT)  # 18 bytes

# Packet types
DATA   = 0
ACK    = 1
SYN    = 2
FIN    = 3
SYNACK = 4

TYPE_NAMES = {DATA: "DATA", ACK: "ACK", SYN: "SYN", FIN: "FIN", SYNACK: "SYNACK"}

MAX_SEGMENT_SIZE = 8192          # bytes of payload per packet (optimized for media)
MAX_WINDOW_SIZE  = 64            # packets
TIMEOUT          = 0.5           # seconds


class Packet:
    def __init__(self, ptype, seq_num=0, ack_num=0, win_size=MAX_WINDOW_SIZE,
                 data=b"", flags=0):
        self.ptype    = ptype
        self.flags    = flags
        self.seq_num  = seq_num
        self.ack_num  = ack_num
        self.win_size = win_size
        self.data     = data if isinstance(data, bytes) else data.encode()

    # ── serialization ────────────────────────────────────────────────────────

    def to_bytes(self) -> bytes:
        raw = struct.pack(
            HEADER_FORMAT,
            self.ptype,
            self.flags,
            0,                  # checksum placeholder
            self.seq_num,
            self.ack_num,
            self.win_size,
            len(self.data),
            0,                  # padding / reserved
        ) + self.data

        checksum = zlib.crc32(raw) & 0xFFFF
        # patch checksum in at offset 2
        return raw[:2] + struct.pack("!H", checksum) + raw[4:]

    @staticmethod
    def from_bytes(raw: bytes):
        if len(raw) < HEADER_SIZE:
            return None

        unpacked = struct.unpack(HEADER_FORMAT, raw[:HEADER_SIZE])
        ptype, flags, recv_checksum, seq_num, ack_num, win_size, data_len, _ = unpacked
        data = raw[HEADER_SIZE: HEADER_SIZE + data_len]

        # verify checksum
        patched = raw[:2] + b"\x00\x00" + raw[4:]
        expected = zlib.crc32(patched) & 0xFFFF
        if expected != recv_checksum:
            return None   # corrupted

        p = Packet(ptype, seq_num, ack_num, win_size, data, flags)
        return p

    # ── helpers ──────────────────────────────────────────────────────────────

    def __repr__(self):
        name = TYPE_NAMES.get(self.ptype, "?")
        return (f"Packet({name} seq={self.seq_num} ack={self.ack_num} "
                f"win={self.win_size} datalen={len(self.data)})")
