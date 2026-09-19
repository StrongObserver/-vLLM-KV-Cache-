"""Create the complete arrival plan before any service measurements.

Token-ID prompts avoid silently changing lengths through string retokenization.
These are synthetic load prompts, not a semantic-quality dataset.
"""

import argparse
import hashlib
import json
import math
import random
from dataclasses import asdict
from pathlib import Path

from .schema import PlannedRequest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tokenizer", default="Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument("--revision", required=True, help="Frozen tokenizer commit")
    parser.add_argument("--seed", type=int, default=19)
    parser.add_argument("--duration", type=float, default=1800)
    parser.add_argument("--rate", type=float, default=5)
    parser.add_argument("--scenario", choices=["mixed", "shared", "burst", "homogeneous"],
                        default="mixed")
    args = parser.parse_args()
    if args.duration <= 0 or args.rate <= 0:
        parser.error("duration and rate must be positive")
    if args.output.exists():
        parser.error("output directory must be new to preserve frozen traces")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, revision=args.revision)
    excluded = set(tokenizer.all_special_ids)
    vocab = sorted(set(tokenizer.get_vocab().values()) - excluded)
    rng = random.Random(args.seed)
    count = math.ceil(args.duration * args.rate)
    short_count, long_count = int(count * .60), int(count * .25)
    groups = (["short"] * short_count + ["long_input"] * long_count
              + ["long_output"] * (count - short_count - long_count))
    if args.scenario == "homogeneous":
        groups = ["short"] * count
    rng.shuffle(groups)
    shapes = {"short": (512, 256), "long_input": (8192, 256),
              "long_output": (512, 4096)}
    shared = rng.choices(vocab, k=256)
    args.output.mkdir(parents=True)
    plan_file = args.output / "plan.jsonl"
    digest = hashlib.sha256()
    with plan_file.open("w") as output:
        for index, group in enumerate(groups):
            release = index / args.rate
            if args.scenario == "burst":
                release = math.floor(release)  # Same mean rate, batched arrivals.
            input_tokens, output_tokens = shapes[group]
            prefix = shared if args.scenario == "shared" else []
            prompt = prefix + rng.choices(vocab, k=input_tokens - len(prefix))
            row = PlannedRequest(f"s{args.seed}-{index:06d}", release, group,
                                 prompt, output_tokens)
            line = json.dumps(asdict(row), separators=(",", ":")) + "\n"
            output.write(line)
            digest.update(line.encode())
    metadata = {
        "schema_version": 1, "model": args.tokenizer,
        "tokenizer_revision": args.revision, "seed": args.seed,
        "scenario": args.scenario, "duration_s": args.duration,
        "rate_rps": args.rate, "planned_requests": count,
        "plan_sha256": digest.hexdigest(), "deadline_s": 300,
        "ttft_budget_s": {"short": 2, "long_input": 10, "long_output": 2},
        "tpot_budget_s": .050,
        "generator_limits": {"p99_release_lag_s": .005, "max_release_lag_s": .020},
    }
    (args.output / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
