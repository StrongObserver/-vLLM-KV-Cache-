# 来源与实现边界

## 直接复用

**vLLM，Apache-2.0**：[固定提交](https://github.com/vllm-project/vllm/tree/fa008bdccf10f2f31f84553112a227cf1b8947d7)。`scheduler.py`、`kv_cache_manager.py` 由该提交导入后修改；原始 SHA-256 在 `upstream.lock`，版权头与完整 LICENSE 保留。其他页管理器、队列、模型执行和请求结构通过固定上游依赖使用。

容量口径来自 [`get_num_blocks_to_allocate`](https://github.com/vllm-project/vllm/blob/fa008bdccf10f2f31f84553112a227cf1b8947d7/vllm/v1/core/single_type_kv_cache_manager.py)，通过原分配器直接复用，不用简化公式替代。

## 设计与协议参考

以下资料分别指导队列扫描、传输和测量；不表示复制这些工程的全部实现，也不表示其验证过本项目。

| 来源 | 采用范围 | 本仓库落点 |
|---|---|---|
| [Slurm backfill](https://slurm.schedmd.com/sched_config.html) | 有界候选检查、资源受限时考虑后继 | `policy.py`；没有 Slurm 的时长预测或预约机制，不承诺不延迟队首 |
| [vLLM watermark PR #44594](https://github.com/vllm-project/vllm/pull/44594) | 等待准入保留容量余量 | 保留固定版分配器行为，不列为本项目原创 |
| [WHATWG SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html) | 空行结束、多行 data、注释与 UTF-8 | `benchmarks/sse.py`；面向 vLLM LF/CRLF 流，不实现浏览器重连 |
| [NVIDIA GenAI-Perf](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/perf_analyzer/genai-perf/README.html) | 请求级时延、token 计数、负载与服务观测分离 | `replay.py`、`evaluate.py`；另按留存资料实现固定 cohort goodput，TPOT 不冒充 ITL |

`kv_admission/`、回放/报告与源码组装工具为本次编写。多个来源分别解决不同模块的问题；核心引擎仍固定为 vLLM，不混入互相冲突的页分配器。
