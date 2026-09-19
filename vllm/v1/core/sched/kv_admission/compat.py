# SPDX-License-Identifier: Apache-2.0
"""Fail closed to the original scheduler outside the audited KV contract."""

from vllm.v1.core.single_type_kv_cache_manager import FullAttentionManager
from vllm.v1.kv_cache_interface import FullAttentionSpec
from vllm.v1.request import Request, RequestStatus


def unsupported_reasons(scheduler) -> tuple[str, ...]:
    config = scheduler.vllm_config
    parallel = config.parallel_config
    model = config.model_config
    groups = scheduler.kv_cache_config.kv_cache_groups
    reasons = []
    checks = {
        "requires synchronous scheduling": bool(config.scheduler_config.async_scheduling),
        "requires FCFS": config.scheduler_config.policy != "fcfs",
        "requires one GPU": any(
            getattr(parallel, name, 1) != 1
            for name in (
                "tensor_parallel_size", "pipeline_parallel_size",
                "data_parallel_size", "decode_context_parallel_size",
                "prefill_context_parallel_size",
            )
        ),
        "KV connector": config.kv_transfer_config is not None,
        "encoder connector": scheduler.ec_connector is not None,
        "speculative decoding": config.speculative_config is not None,
        "LoRA": config.lora_config is not None,
        "multimodal": bool(getattr(model, "is_multimodal_model", False)),
        "encoder-decoder": model.is_encoder_decoder,
        "diffusion": model.is_diffusion,
        "pooling": getattr(model, "runner_type", "generate") != "generate",
        "fine-grained prefix hashing": scheduler.hash_block_size != scheduler.block_size,
        "requires one full-attention KV group": len(groups) != 1,
    }
    for reason, unsupported in checks.items():
        if unsupported:
            reasons.append(reason)
    for group in groups:
        spec = group.kv_cache_spec
        if (type(spec) is not FullAttentionSpec or spec.sliding_window is not None
                or spec.attention_chunk_size is not None):
            reasons.append("non-dense full-attention KV spec")
            break
    # Exact manager type excludes derived classes that release middle/old pages.
    if any(type(manager) is not FullAttentionManager for manager in
           scheduler.kv_cache_manager.coordinator.single_type_managers):
        reasons.append("KV manager has an unaudited rejection contract")
    return tuple(reasons)


def request_is_eligible(request: Request) -> bool:
    """Candidate filters do not promote status or touch allocator state."""
    return (
        request.status in (RequestStatus.WAITING, RequestStatus.PREEMPTED)
        and not request.has_encoder_inputs
        and request.lora_request is None
        and request.structured_output_request is None
        and request.num_stale_output_tokens == 0
        and request.num_in_flight_tokens == 0
        and request.num_computed_tokens == 0
        and getattr(request, "streaming_queue", None) is None
    )
