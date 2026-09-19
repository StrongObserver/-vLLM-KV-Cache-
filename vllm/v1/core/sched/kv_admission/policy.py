# SPDX-License-Identifier: Apache-2.0
"""Queue-order policy, deliberately independent of page-demand arithmetic.

Only the scheduler owner calls these methods. A round snapshots request
references, never prefix block IDs. Allocation exceptions must propagate.
"""

from itertools import islice

from .config import AdmissionConfig
from .metrics import AdmissionMetrics


class AdmissionPolicy:
    def __init__(self, config: AdmissionConfig, fallback_reasons=()) -> None:
        self.config = config
        self.fallback_reasons = tuple(fallback_reasons)
        self.metrics = AdmissionMetrics()
        # Lifetime is a logical request, spanning all preemptions and resumes.
        self.bypass_counts: dict[str, int] = {}
        self.recompute_until: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        return self.config.mode != "fcfs" and not self.fallback_reasons

    def begin_round(self, allow_scan: bool) -> "AdmissionRound":
        self.metrics.counts["rounds"] += 1
        return AdmissionRound(self, self.enabled and allow_scan)

    def on_preempt(self, request_id: str, computed_tokens: int) -> None:
        # Do not delete or reset bypass_counts here.
        self.metrics.counts["preemptions"] += 1
        self.metrics.counts["preempted_computed_tokens"] += computed_tokens
        self.recompute_until[request_id] = computed_tokens

    def on_scheduled(self, request_id: str, start: int, count: int) -> None:
        """Count actual scheduled replay below the last preemption frontier.

        A fresh prefix hit advances start and therefore is not recomputation.
        This is scheduled work, not measured GPU time or confirmed execution.
        """
        frontier = self.recompute_until.get(request_id)
        if frontier is None:
            return
        self.metrics.counts["scheduled_recompute_tokens"] += max(
            0, min(start + count, frontier) - start
        )
        if start + count >= frontier:
            self.recompute_until.pop(request_id, None)

    def on_finish(self, request_id: str) -> None:
        self.bypass_counts.pop(request_id, None)
        self.recompute_until.pop(request_id, None)


class AdmissionRound:
    def __init__(self, policy: AdmissionPolicy, allow_scan: bool) -> None:
        self.policy = policy
        self.allow_scan = allow_scan
        self.active = False
        self.remaining = policy.config.window
        self._candidates = iter(())
        self.predecessors = []

    def reject_capacity(self, request, queue, can_start: bool) -> bool:
        """Return whether the caller should continue with a fresh candidate.

        A normal rejection changes only diagnostics and this round's private
        cursor; it never mutates the real queue, page pool or bypass counts.
        """
        self.policy.metrics.counts["kv_rejections"] += 1
        if self.active:
            self.predecessors.append(request)
            return True
        if not self.allow_scan or not can_start:
            return False
        with self.policy.metrics.measure("window_snapshot"):
            assert queue.peek_request() is request
            self._candidates = iter(tuple(islice(queue, 1, self.remaining + 1)))
        self.active = True
        self.predecessors.append(request)
        self.policy.metrics.counts["blocked_heads"] += 1
        return True

    def next_candidate(self):
        if not self.active or not self.remaining:
            return None
        with self.policy.metrics.measure("barrier_and_cursor"):
            if self.policy.config.mode == "bounded" and any(
                self.policy.bypass_counts.get(p.request_id, 0)
                >= self.policy.config.bypass_limit for p in self.predecessors
            ):
                self.policy.metrics.counts["barrier_stops"] += 1
                return None
            request = next(self._candidates, None)
            if request is not None:
                self.remaining -= 1
                self.policy.metrics.counts["positions_checked"] += 1
            return request

    def skip_candidate(self, request, reason: str) -> None:
        # Skips consume original positions and remain protected predecessors.
        self.predecessors.append(request)
        self.policy.metrics.counts[f"skip_{reason}"] += 1

    def remove_committed(self, queue, request) -> None:
        with self.policy.metrics.measure("queue_commit"):
            if self.active:
                # Upstream FCFS deque.remove is O(N); W bounds lookups, not this.
                queue.remove_request(request)
            else:
                removed = queue.pop_request()
                assert removed is request

    def on_admitted(self, request) -> None:
        self.policy.metrics.counts["admissions"] += 1
        if not self.active:
            return
        with self.policy.metrics.measure("bypass_update"):
            self.policy.metrics.counts["out_of_order_admissions"] += 1
            for predecessor in self.predecessors:
                key = predecessor.request_id
                self.policy.bypass_counts[key] = (
                    self.policy.bypass_counts.get(key, 0) + 1
                )
                self.policy.metrics.counts["successful_bypasses"] += 1
