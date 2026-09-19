# SPDX-License-Identifier: Apache-2.0
"""Policy knobs; allocator and prefill knobs remain owned by vLLM."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AdmissionConfig:
    mode: str = "fcfs"
    window: int = 8
    bypass_limit: int = 8
    log_interval: int = 1000

    def __post_init__(self) -> None:
        if self.mode not in {"fcfs", "window", "bounded"}:
            raise ValueError("KV_ADMISSION_MODE must be fcfs, window or bounded")
        if not 1 <= self.window <= 64:
            raise ValueError("KV_ADMISSION_WINDOW must be in [1, 64]")
        if self.bypass_limit < 1 or self.log_interval < 0:
            raise ValueError("bypass limit must be positive; log interval >= 0")

    @classmethod
    def from_env(cls) -> "AdmissionConfig":
        return cls(
            mode=os.getenv("KV_ADMISSION_MODE", "fcfs"),
            window=int(os.getenv("KV_ADMISSION_WINDOW", "8")),
            bypass_limit=int(os.getenv("KV_ADMISSION_BYPASS_LIMIT", "8")),
            log_interval=int(os.getenv("KV_ADMISSION_LOG_INTERVAL", "1000")),
        )
