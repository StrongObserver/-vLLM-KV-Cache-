"""Left-join observations onto ALL planned IDs; never evaluate successes alone."""

import argparse
import json
import math
from collections import Counter
from pathlib import Path


def quantile(values: list[float], q: float) -> float | None:
    """Nearest-rank, including the ceil(0.99 * N) convention for P99."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def load_observations(directory: Path) -> dict[str, dict]:
    snapshot = directory / "observations_snapshot.jsonl"
    source = snapshot if snapshot.exists() else directory / "observations.jsonl"
    result = {}
    if source.exists():
        for line in source.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                # A crash can leave a partial last journal entry. Its planned
                # request remains present, with an unknown/failed observation.
                continue
            if row["request_id"] in result:
                raise ValueError("Duplicate observation; retries must be separate")
            result[row["request_id"]] = row
    return result


def summarize(directory: Path) -> dict:
    manifest = json.loads((directory / "trace_manifest.json").read_text())
    run = json.loads((directory / "run.json").read_text())
    cohort = [json.loads(line) for line in
              (directory / "cohort.jsonl").read_text().splitlines() if line]
    ids = [row["request_id"] for row in cohort]
    if len(ids) != len(set(ids)) or len(ids) != manifest["planned_requests"]:
        raise ValueError("Cohort must retain every unique planned ID")
    duration = manifest["duration_s"]
    if any(not 0 <= row["release_s"] < duration for row in cohort):
        raise ValueError("Cohort lies outside the frozen arrival window")
    observations = load_observations(directory)
    if set(observations) - set(ids):
        raise ValueError("Result contains requests outside the frozen cohort")
    groups = {}
    release_lags = []
    status_counts = Counter()
    attempted = completed = good = late = delivered_tokens = 0
    early = 0
    attempts_per_second = Counter()
    for request in cohort:
        row = observations.get(request["request_id"], {})
        group = groups.setdefault(request["group"], {
            "planned": 0, "attempted": 0, "completed": 0, "good": 0,
            "ttft": [], "planned_ttft": [], "completed_tpot": [],
            "no_first_token_lower_bounds": [],
        })
        group["planned"] += 1
        status = row.get("completion_status", "missing_observation")
        status_counts[status] += 1
        sent = row.get("actual_attempt") if row.get("attempted") else None
        first, last = row.get("first_token"), row.get("last_token")
        for value in (sent, first, last, row.get("completed_at")):
            if value is not None and (not isinstance(value, (int, float))
                                      or not math.isfinite(value)):
                raise ValueError("Non-finite or nonnumeric timestamp")
        if sent is None:
            continue
        attempted += 1
        group["attempted"] += 1
        lag = sent - request["release_s"]
        release_lags.append(lag)
        early += int(lag < 0)
        late += int(sent >= duration)
        attempts_per_second[int(sent)] += 1
        ttft = first - sent if first is not None else None
        if ttft is not None:
            if ttft < 0:
                raise ValueError("First token precedes request send")
            # Includes requests which later fail or time out.
            group["ttft"].append(ttft)
            group["planned_ttft"].append(first - request["release_s"])
        else:
            until = row.get("observed_until")
            if until is not None:
                group["no_first_token_lower_bounds"].append(max(0, until - sent))
        n = row.get("output_tokens", 0)
        tpot = ((last - first) / (n - 1)
                if n > 1 and first is not None and last is not None else None)
        if tpot is not None and tpot < 0:
            raise ValueError("Last token precedes first token")
        completed_at = row.get("completed_at")
        ok = (status == "completed" and n == request["output_tokens"]
              and first is not None and last is not None
              and completed_at is not None and completed_at >= last
              and completed_at - sent <= manifest["deadline_s"])
        if ok:
            completed += 1
            group["completed"] += 1
            delivered_tokens += n
            if tpot is not None:
                group["completed_tpot"].append(tpot)
        meets_slo = (ok and ttft is not None and tpot is not None
                     and ttft <= manifest["ttft_budget_s"][request["group"]]
                     and tpot <= manifest["tpot_budget_s"])
        good += int(meets_slo)
        group["good"] += int(meets_slo)

    group_reports = {}
    for name, data in groups.items():
        observed = len(data["ttft"])
        missing = data["planned"] - observed
        group_reports[name] = {
            "planned": data["planned"], "attempted": data["attempted"],
            "completed": data["completed"], "slo_passed": data["good"],
            "completion_fraction": data["completed"] / data["planned"],
            "observed_first_token": observed, "no_first_token": missing,
            "no_first_token_fraction": missing / data["planned"],
            "conditional_ttft_p99_s": quantile(data["ttft"], .99),
            "all_request_ttft_p99_s": quantile(data["ttft"], .99) if not missing else None,
            "planned_to_first_token_p99_s": quantile(data["planned_ttft"], .99),
            "completed_mean_tpot_p50_s": quantile(data["completed_tpot"], .50),
            "completed_mean_tpot_p99_s": quantile(data["completed_tpot"], .99),
            "no_first_token_observation_lower_bounds_s": data["no_first_token_lower_bounds"],
        }
    limits = manifest["generator_limits"]
    lag_p99 = quantile(release_lags, .99)
    lag_max = max(release_lags, default=None)
    valid = (attempted == len(cohort) and not early and not late
             and lag_p99 is not None and lag_p99 <= limits["p99_release_lag_s"]
             and lag_max <= limits["max_release_lag_s"])
    elapsed = run.get("observed_duration_s")
    return {
        "schema_version": 1, "label": run["label"],
        "trace_sha256": manifest["plan_sha256"], "duration_s": duration,
        "run_finished": run["run_status"] == "finished",
        "generator_limited": not valid,
        "eligible_for_comparison": valid and run["run_status"] == "finished",
        "planned": len(cohort), "attempted": attempted, "completed": completed,
        "slo_passed": good, "diagnostic_goodput_rps": good / duration,
        "planned_slo_fraction": good / len(cohort),
        "completed_cohort_rps": completed / duration,
        "completed_cohort_output_tokens_per_s": delivered_tokens / duration,
        "drain_inclusive_completion_rps": completed / elapsed if elapsed else None,
        "release_lag_p99_s": lag_p99, "release_lag_max_s": lag_max,
        "late_attempts": late, "early_attempts": early,
        "attempts_per_second": dict(sorted(attempts_per_second.items())),
        "completion_status_counts": dict(status_counts), "groups": group_reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_directory", type=Path)
    args = parser.parse_args()
    report = summarize(args.result_directory)
    (args.result_directory / "summary.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: report[key] for key in (
        "label", "eligible_for_comparison", "diagnostic_goodput_rps",
        "planned", "attempted", "completed", "slo_passed")}, indent=2))


if __name__ == "__main__":
    main()
