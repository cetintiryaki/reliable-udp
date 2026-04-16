"""
congestion.py - TCP-like congestion control state machine

Implements:
  - Slow Start
  - Congestion Avoidance
  - Fast Retransmit  (on 3 duplicate ACKs)
  - Fast Recovery
"""

from enum import Enum, auto


class CCState(Enum):
    SLOW_START          = auto()
    CONGESTION_AVOIDANCE = auto()
    FAST_RECOVERY       = auto()


class CongestionController:
    """
    Tracks cwnd (congestion window) and ssthresh.
    The effective send window = min(cwnd, rwnd).
    """

    def __init__(self, initial_ssthresh: int = 32):
        self.cwnd       = 1           # packets
        self.ssthresh   = initial_ssthresh
        self.state      = CCState.SLOW_START
        self.dup_acks   = 0
        self._log: list[str] = []

    # ── public API ───────────────────────────────────────────────────────────

    def on_ack(self, is_duplicate: bool = False) -> None:
        """Call once per received ACK."""
        if is_duplicate:
            self._handle_dup_ack()
        else:
            self.dup_acks = 0
            if self.state == CCState.SLOW_START:
                self._slow_start_ack()
            elif self.state == CCState.CONGESTION_AVOIDANCE:
                self._ca_ack()
            elif self.state == CCState.FAST_RECOVERY:
                # New ACK → exit fast recovery
                self.cwnd  = self.ssthresh
                self.state = CCState.CONGESTION_AVOIDANCE
                self._log_state("exit fast-recovery → CA")

    def on_timeout(self) -> None:
        """Call on retransmission timeout."""
        self.ssthresh = max(self.cwnd // 2, 1)
        self.cwnd     = 1
        self.dup_acks = 0
        self.state    = CCState.SLOW_START
        self._log_state("TIMEOUT → slow-start")

    @property
    def window(self) -> int:
        return max(1, self.cwnd)

    def log(self) -> list[str]:
        return list(self._log)

    # ── internals ────────────────────────────────────────────────────────────

    def _slow_start_ack(self) -> None:
        self.cwnd += 1
        if self.cwnd >= self.ssthresh:
            self.state = CCState.CONGESTION_AVOIDANCE
            self._log_state("SS → CA")
        else:
            self._log_state("SS ack")

    def _ca_ack(self) -> None:
        # Increase by 1/cwnd per ACK  ≈ +1 per RTT
        self.cwnd += 1 / self.cwnd
        self._log_state("CA ack")

    def _handle_dup_ack(self) -> None:
        self.dup_acks += 1
        if self.dup_acks == 3:
            # Enter fast retransmit / fast recovery
            self.ssthresh = max(self.cwnd // 2, 1)
            self.cwnd     = self.ssthresh + 3
            self.state    = CCState.FAST_RECOVERY
            self._log_state("3 dup-ACKs → fast-retransmit")
        elif self.state == CCState.FAST_RECOVERY:
            self.cwnd += 1        # inflate window during fast recovery
            self._log_state("FR inflate")

    def _log_state(self, event: str) -> None:
        self._log.append(
            f"[{event}] cwnd={self.cwnd:.2f} ssthresh={self.ssthresh} state={self.state.name}"
        )
