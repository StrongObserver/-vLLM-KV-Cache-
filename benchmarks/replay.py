"""Replay frozen arrivals against an already-running vLLM completion server."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from .schema import Observation, read_plan
from .sse import data_events


async def replay(args) -> None:
    import aiohttp

    manifest_bytes = (args.trace / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    plan_path = args.trace / "plan.jsonl"
    if hashlib.sha256(plan_path.read_bytes()).hexdigest() != manifest["plan_sha256"]:
        raise ValueError("Trace no longer matches its frozen manifest")
    plan = read_plan(plan_path)
    duration = manifest["duration_s"]
    if len(plan) != manifest["planned_requests"] or plan[-1].release_s >= duration:
        raise ValueError("Plan does not match the fixed arrival window")
    if args.output.exists():
        raise ValueError("Result directory must be new")
    args.output.mkdir(parents=True)
    (args.output / "trace_manifest.json").write_bytes(manifest_bytes)
    engine_manifest = json.loads(args.engine_manifest.read_text())
    (args.output / "engine_manifest.json").write_text(
        json.dumps(engine_manifest, indent=2) + "\n")
    # Prepare serialization before starting the clock. There is no semaphore
    # that would accidentally turn open arrivals into closed concurrency.
    payloads = [json.dumps({
        "model": args.model or manifest["model"], "prompt": row.prompt_token_ids,
        "max_tokens": row.output_tokens, "temperature": 0, "seed": 0,
        "ignore_eos": True, "stream": True, "stream_interval": 1,
        "return_token_ids": True, "stream_options": {"include_usage": True},
    }) for row in plan]
    observations = {row.request_id: Observation(row.request_id, row.release_s)
                    for row in plan}
    # Persist ALL planned IDs before sending, including potential never-sent IDs.
    cohort = [{"request_id": row.request_id, "release_s": row.release_s,
               "group": row.group, "input_tokens": len(row.prompt_token_ids),
               "output_tokens": row.output_tokens} for row in plan]
    (args.output / "cohort.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in cohort))
    start = time.monotonic() + 1.0
    run_manifest = {"schema_version": 1, "trace_sha256": manifest["plan_sha256"],
                    "label": args.label, "clock": "client_monotonic_relative_seconds",
                    "run_status": "in_progress", "duration_s": duration,
                    "warmup_attestation": args.warmup_note,
                    "engine_source": engine_manifest["engine_source"],
                    "policy_env": engine_manifest["policy_env"]}
    run_file = args.output / "run.json"
    run_file.write_text(json.dumps(run_manifest, indent=2) + "\n")
    trace_config = aiohttp.TraceConfig()

    async def headers_sent(session, context, params):
        state = context.trace_request_ctx
        record = state["record"]
        record.attempted = True
        record.actual_attempt = time.monotonic() - start
        record.send_status = "headers_sent"
        record.completion_status = "in_flight"
        # This callback occurs after connector queueing/connection setup.
        state["deadline"].reschedule(asyncio.get_running_loop().time()
                                     + manifest["deadline_s"])

    trace_config.on_request_headers_sent.append(headers_sent)
    headers = {"Content-Type": "application/json"}
    if os.getenv("KV_BENCH_API_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["KV_BENCH_API_KEY"]
    connector = aiohttp.TCPConnector(limit=0)
    result_file = (args.output / "observations.jsonl").open("w", buffering=1)

    async def send(session, row, payload):
        record = observations[row.request_id]
        digest = hashlib.sha256()
        done = False
        usage_count = None
        try:
            # Initial timeout also caps a connection that never sends headers.
            async with asyncio.timeout(manifest["deadline_s"]) as deadline:
                async with session.post(
                    args.endpoint, data=payload,
                    trace_request_ctx={"record": record, "deadline": deadline},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        record.send_status = f"http_{response.status}"
                        record.completion_status = "service_error"
                        record.error = (await response.text())[:1000]
                        return
                    async for data in data_events(response.content):
                        if data == "[DONE]":
                            done = True
                            break
                        event = json.loads(data)
                        if "error" in event:
                            record.completion_status = "service_error"
                            record.error = str(event["error"])[:1000]
                            return
                        usage = event.get("usage")
                        if usage:
                            usage_count = usage.get("completion_tokens")
                        for choice in event.get("choices", []):
                            token_ids = choice.get("token_ids")
                            if token_ids is None and choice.get("text"):
                                raise ValueError("Server omitted delta token_ids")
                            if token_ids:
                                now = time.monotonic() - start
                                if record.first_token is None:
                                    record.first_token = now
                                record.last_token = now
                                record.output_tokens += len(token_ids)
                                for token in token_ids:
                                    digest.update(int(token).to_bytes(8, "little"))
                            if choice.get("finish_reason") is not None:
                                record.finish_reason = choice["finish_reason"]
                    record.completed_at = time.monotonic() - start
                    complete = (done and record.finish_reason == "length"
                                and record.output_tokens == row.output_tokens
                                and usage_count == record.output_tokens)
                    record.completion_status = "completed" if complete else "incomplete"
        except TimeoutError:
            record.completion_status = "timeout"
        except aiohttp.ClientConnectorError as error:
            record.completion_status = "service_unavailable"
            record.error = type(error).__name__
        except (aiohttp.ClientError, OSError) as error:
            record.completion_status = "transport_error"
            record.error = type(error).__name__
        except (ValueError, KeyError, TypeError) as error:
            record.completion_status = "protocol_error"
            record.error = str(error)[:1000]
        except asyncio.CancelledError:
            record.completion_status = "client_cancelled"
            raise
        finally:
            record.observed_until = time.monotonic() - start
            record.output_sha256 = digest.hexdigest()
            result_file.write(json.dumps(record.to_dict()) + "\n")

    tasks = []
    try:
        async with aiohttp.ClientSession(
            connector=connector, headers=headers, trace_configs=[trace_config],
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
        ) as session:
            for row, payload in zip(plan, payloads):
                await asyncio.sleep(max(0, start + row.release_s - time.monotonic()))
                if time.monotonic() >= start + duration:
                    # Drain never backfills missed arrivals.
                    break
                tasks.append(asyncio.create_task(send(session, row, payload)))
            await asyncio.gather(*tasks)
        run_manifest["run_status"] = "finished"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        result_file.close()
        # Snapshot includes unsent requests even on interruption. An abrupt
        # process kill is handled by the evaluator's left join to cohort.jsonl.
        (args.output / "observations_snapshot.jsonl").write_text(
            "".join(json.dumps(row.to_dict()) + "\n" for row in observations.values()))
        run_manifest["observed_duration_s"] = max(0, time.monotonic() - start)
        run_file.write_text(json.dumps(run_manifest, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--engine-manifest", type=Path, required=True)
    parser.add_argument("--warmup-note", required=True,
                        help="Record completed warmup protocol and artifact path")
    parser.add_argument("--model")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8000/v1/completions")
    asyncio.run(replay(parser.parse_args()))


if __name__ == "__main__":
    main()
