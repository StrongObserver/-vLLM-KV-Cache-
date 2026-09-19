#!/usr/bin/env python3
"""Extract cumulative scheduler snapshots; never sum cumulative samples."""

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path)
    args = parser.parse_args()
    samples = []
    decoder = json.JSONDecoder()
    for line in args.log.read_text().splitlines():
        if "kv_admission=" not in line:
            continue
        payload = line.split("kv_admission=", 1)[1]
        sample, _ = decoder.raw_decode(payload)
        if samples and sample["step"] <= samples[-1]["step"]:
            raise ValueError("Log mixes restarted engines; split it by process lifetime")
        samples.append(sample)
    if len(samples) < 2:
        raise ValueError("Need at least two cumulative snapshots for an interval delta")
    first, last = samples[0], samples[-1]
    print(json.dumps({
        "first_sample_step": first["step"], "last_sample_step": last["step"],
        "counters_delta": {key: value - first["counters"].get(key, 0)
                           for key, value in last["counters"].items()},
        "host_time_ns_delta": {key: value - first["host_time_ns"].get(key, 0)
                              for key, value in last["host_time_ns"].items()},
        "note": "Sampled server interval; align explicitly with the client cohort.",
    }, indent=2))


if __name__ == "__main__":
    main()
