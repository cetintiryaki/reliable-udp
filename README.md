# Reliable UDP Transport Protocol

A TCP-like reliable transport layer built on top of UDP, implemented in Python from scratch.

## Features

| Feature | Details |
|---|---|
| **Reliable delivery** | SEQ/ACK numbering, checksum verification (CRC32) |
| **Sliding window** | Go-Back-N with out-of-order buffering |
| **Retransmission** | Timeout-based + Fast Retransmit (3 duplicate ACKs) |
| **Flow control** | Receiver advertises RWND; sender respects it |
| **Congestion control** | Slow Start → Congestion Avoidance → Fast Recovery |
| **Connection setup** | 3-way handshake (SYN / SYNACK / ACK) |
| **Loss simulation** | Configurable packet drop rate for testing |

## Architecture

```
reliable-udp/
├── packet.py         # Packet format, serialization, checksum
├── sender.py         # Sliding window sender with congestion control
├── receiver.py       # Buffered receiver with flow control
├── congestion.py     # Congestion control state machine (SS / CA / FR)
├── file_transfer.py  # File transfer application + loopback demo
└── chat.py           # Terminal chat application
```

### Packet Header (18 bytes)

```
 0       1       2       3       4
 ┌───────┬───────┬───────────────┬───────────────────────────────┐
 │ type  │ flags │   checksum    │           seq_num             │
 ├───────────────────────────────┼───────────────────────────────┤
 │           ack_num             │  win_size  │  data_len  │ pad │
 └───────────────────────────────┴────────────┴────────────┴─────┘
```

## Quick Start

**Requirements:** Python 3.8+, no external libraries needed.

### File Transfer

```bash
# Terminal 1 — receiver
python file_transfer.py receive 9000 output.bin

# Terminal 2 — sender (with 5% simulated packet loss)
python file_transfer.py send 127.0.0.1 9000 myfile.txt 0.05
```

### Self-contained Demo (single terminal)

```bash
python file_transfer.py demo myfile.txt 0.1
```

### Terminal Chat

```bash
# Terminal 1 (listen on 9001, talk to 9002)
python chat.py 9001 127.0.0.1 9002

# Terminal 2 (listen on 9002, talk to 9001)
python chat.py 9002 127.0.0.1 9001
```

## Congestion Control

The sender implements the classic TCP congestion control algorithm:

```
cwnd
  ^
32│          . . . . . . . .   ← Congestion Avoidance (+1/cwnd per ACK)
  │        .
16│      .
  │    .
  │  .                          ← Slow Start (doubles each RTT)
 1└──────────────────────────────────────────> time
     ssthresh reached → switch to CA
```

- **Slow Start:** `cwnd` doubles each RTT until `ssthresh`
- **Congestion Avoidance:** `cwnd` increases by ~1 per RTT
- **Timeout:** `ssthresh = cwnd/2`, `cwnd = 1`, restart Slow Start
- **3 Duplicate ACKs:** Fast Retransmit + Fast Recovery (`cwnd = ssthresh + 3`)

## Design Decisions

- **CRC32 checksum** over the full packet for corruption detection
- **Cumulative ACKs** — receiver always ACKs the next expected seq
- **Out-of-order buffering** — receiver buffers up to `rwnd` packets
- **Simulated loss** is applied at the send side for realistic testing
