# SPDX-License-Identifier: Apache-2.0
"""Cumulative host counters; emission is optional and never contains prompts."""

import json
from collections import Counter
from contextlib import contextmanager
from time import perf_counter_ns


class AdmissionMetrics:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.time_ns: Counter[str] = Counter()

    @contextmanager
    def measure(self, phase: str):
        start = perf_counter_ns()
        try:
            yield
        finally:
            self.time_ns[phase] += perf_counter_ns() - start

    def log_if_due(self, logger, step: int, interval: int, mode: str) -> None:
        if interval and step % interval == 0:
            logger.info("kv_admission=%s", json.dumps({
                "schema_version": 1, "step": step, "mode": mode,
                "counters": dict(self.counts), "host_time_ns": dict(self.time_ns),
            }, sort_keys=True))
