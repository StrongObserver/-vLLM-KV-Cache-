#!/usr/bin/env python3
"""Render a pinned serving configuration; launch only with --execute."""

import argparse
import hashlib
import json
import os
import shlex
import sys
import tomllib
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--mode", choices=["fcfs", "window", "bounded"], default="bounded")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    config = tomllib.loads(args.config.read_text())
    source = args.source_root.resolve()
    source_manifest = json.loads((source / "kv_admission_build_manifest.json").read_text())
    for relative, expected in source_manifest["source_sha256"].items():
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Assembled engine source changed: {relative}")
    serve = config["serve"]
    command = [sys.executable, "-m", "vllm.entrypoints.cli.main", "serve", serve["model"],
               "--revision", args.model_revision, "--tokenizer-revision", args.model_revision]
    for name, value in serve.items():
        if name == "model":
            continue
        flag = name.replace("_", "-")
        if isinstance(value, bool):
            command.append("--" + ("" if value else "no-") + flag)
        else:
            command.extend(["--" + flag, str(value)])
    admission = config["admission"]
    additions = {
        "KV_ADMISSION_MODE": args.mode,
        "KV_ADMISSION_WINDOW": str(admission["window"]),
        "KV_ADMISSION_BYPASS_LIMIT": str(admission["bypass_limit"]),
        "KV_ADMISSION_LOG_INTERVAL": str(admission["log_interval"]),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with args.manifest.open("x") as output:
        json.dump({"command": command, "policy_env": additions,
                   "engine_source": source_manifest,
                   "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
                   "status": "launch_requested" if args.execute else "command_only"},
                  output, indent=2)
        output.write("\n")
    print(shlex.join(command), flush=True)
    print("Policy environment: " + json.dumps(additions), flush=True)
    if args.execute:
        environment = os.environ.copy()
        environment.update(additions)
        environment["PYTHONPATH"] = str(source) + os.pathsep + environment.get("PYTHONPATH", "")
        os.chdir(source)
        os.execve(sys.executable, command, environment)


if __name__ == "__main__":
    main()
