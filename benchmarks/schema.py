"""JSONL contracts shared by the generator, streaming client and report."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlannedRequest:
    request_id: str
    release_s: float
    group: str
    prompt_token_ids: list[int]
    output_tokens: int


@dataclass
class Observation:
    request_id: str
    planned_release: float
    attempted: bool = False
    actual_attempt: float | None = None
    first_token: float | None = None
    last_token: float | None = None
    completed_at: float | None = None
    observed_until: float | None = None
    send_status: str = "not_attempted"
    completion_status: str = "not_attempted"
    output_tokens: int = 0
    output_sha256: str | None = None
    finish_reason: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def read_plan(path: Path) -> list[PlannedRequest]:
    rows = [PlannedRequest(**json.loads(line)) for line in path.read_text().splitlines()
            if line.strip()]
    ids = [row.request_id for row in rows]
    if not rows or len(ids) != len(set(ids)):
        raise ValueError("Plan must contain unique request IDs")
    if any(row.release_s < 0 or not row.prompt_token_ids or row.output_tokens < 2
           for row in rows):
        raise ValueError("Invalid release, prompt or output length")
    if any(a.release_s > b.release_s for a, b in zip(rows, rows[1:])):
        raise ValueError("Plan must be sorted by release time")
    return rows
