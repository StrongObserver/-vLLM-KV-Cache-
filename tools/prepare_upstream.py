#!/usr/bin/env python3
"""Assemble a fresh pinned vLLM checkout plus this source overlay; never build."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(directory), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True,
                        help="New directory; existing checkouts are never overwritten")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = args.target.resolve()
    lock = json.loads((root / "upstream.lock").read_text())
    if target.exists():
        parser.error("target already exists; choose a new path")
    target.mkdir(parents=True)
    git(target, "init")
    git(target, "remote", "add", "origin", lock["repository"])
    git(target, "fetch", "--depth=1", "origin", lock["commit"])
    git(target, "checkout", "--detach", "FETCH_HEAD")
    if git(target, "rev-parse", "HEAD") != lock["commit"]:
        raise RuntimeError("Fetched checkout differs from upstream.lock")
    for relative, expected in lock["overlay_files"].items():
        actual = hashlib.sha256((target / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"Upstream source hash mismatch: {relative}")
    copied = {}
    for source in sorted((root / "vllm").rglob("*.py")):
        relative = source.relative_to(root)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
    (target / "kv_admission_build_manifest.json").write_text(json.dumps({
        "upstream_commit": lock["commit"],
        "overlay_commit": git(root, "rev-parse", "HEAD"),
        "overlay_dirty": bool(git(root, "status", "--porcelain")),
        "source_sha256": copied,
        "status": "source_assembled_not_built",
    }, indent=2) + "\n")
    print(f"Source assembled at {target}. No installation, compilation or inference ran.")


if __name__ == "__main__":
    main()
