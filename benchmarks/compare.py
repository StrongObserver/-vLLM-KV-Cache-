"""Pair two frozen-cohort runs without manufacturing a pooled P99."""

import argparse
import json
from pathlib import Path

from .evaluate import load_observations, summarize


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args()
    baseline, candidate = summarize(args.baseline), summarize(args.candidate)
    if baseline["trace_sha256"] != candidate["trace_sha256"]:
        raise ValueError("Pairwise comparison requires exactly the same trace")
    left, right = load_observations(args.baseline), load_observations(args.candidate)
    common_completed = [key for key in left.keys() & right.keys()
                        if left[key].get("completion_status") == "completed"
                        and right[key].get("completion_status") == "completed"]
    mismatches = [key for key in common_completed
                  if left[key].get("output_sha256") != right[key].get("output_sha256")]
    base_rate, new_rate = (baseline["diagnostic_goodput_rps"],
                           candidate["diagnostic_goodput_rps"])
    base_long = baseline["groups"].get("long_output")
    new_long = candidate["groups"].get("long_output")
    completion_delta = tpot_delta = None
    if base_long and new_long:
        completion_delta = new_long["completion_fraction"] - base_long["completion_fraction"]
        a, b = (base_long["completed_mean_tpot_p99_s"],
                new_long["completed_mean_tpot_p99_s"])
        if a is not None and b is not None:
            tpot_delta = b - a
    gain = new_rate / base_rate - 1 if base_rate else None
    valid = baseline["eligible_for_comparison"] and candidate["eligible_for_comparison"]
    report = {
        "baseline": baseline["label"], "candidate": candidate["label"],
        "eligible_for_comparison": valid,
        "diagnostic_goodput_relative_change": gain,
        "long_output_completion_fraction_change": completion_delta,
        "long_output_completed_mean_tpot_p99_change_s": tpot_delta,
        "common_completed_outputs": len(common_completed),
        "output_hash_mismatch_ids": mismatches,
        "single_trace_adoption_conditions_met": (
            valid and gain is not None and gain >= .05
            and completion_delta is not None and completion_delta >= -.01
            and tpot_delta is not None and tpot_delta <= .002
            and not mismatches
        ),
        "note": "One paired trace only; rerun on independent traces. No pooled P99.",
    }
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
