# 回放与指标协议

## 固定负载

主 trace：512/256、8192/256、512/4096 输入/输出 token，占比 60%/25%/15%，5 req/s，1800 s 到达窗口。输出长度用于构造负载，调度策略不读取真实未来输出。

`generate_trace.py` 离线保存所有计划 ID、释放时刻及精确 token-ID prompt，冻结 tokenizer revision、随机种子和计划 SHA-256。`mixed` 使用独立随机前缀，`shared` 共享前 256 token，`burst` 把同秒到达集中释放，`homogeneous` 使用同质短请求。低负载与宽 KV 预算通过显式配置生成。

以下命令是供未来使用的入口，本次未运行。客户端依赖列在 `requirements-bench.txt`。

```bash
python3 -m benchmarks.generate_trace \
  --output artifacts/trace-19 --revision TOKENIZER_COMMIT --seed 19
python3 -m benchmarks.replay \
  --trace artifacts/trace-19 --output artifacts/bounded-19 --label bounded \
  --engine-manifest artifacts/bounded-engine.json \
  --warmup-note '实际预热协议和记录路径'
python3 -m benchmarks.evaluate artifacts/bounded-19
python3 -m benchmarks.compare artifacts/fcfs-19 artifacts/bounded-19
```

预热应在正式回放前完成；`--warmup-note` 是实际操作者记录，不是程序自动验证过预热。脚本把引擎源码摘要和配置清单复制到结果目录，但该清单不自动证明远端服务加载了这份代码，应同时留存服务启动日志。

## 时间和 token

计划时刻、实际发送、首 token、末 token、完成确认都使用同一客户端单调时钟，相对固定起点保存。实际发送由 aiohttp 的 headers-sent 回调记录，发生在连接池等待和建连之后；释放迟滞包含本机调度和连接准备的延迟。

客户端按计划创建独立请求，不等待前一个请求完成；不设置隐藏并发信号量。每请求 deadline 在实际发送后为 300 s，建连前也有初始超时，连接阶段另限 10 s。到达窗口结束不补发漏掉的请求，已发请求继续排空。

使用 vLLM completion API 的 `return_token_ids=true` 和 `stream_interval=1`，按 delta token IDs 计数，包括文本可能为空的 token；不把网络包数当 token 数。SSE 的空行分帧由 `sse.py` 处理。没有 token ID 的非空内容流标为协议错误，不用文本分词猜测替代。usage 与收到的 token 数一致、输出达到目标长度且收到结束确认才判完整完成。

- `TTFT = first_token - actual_attempt`
- `TPOT = (last_token - first_token) / (output_tokens - 1)`
- `release_lag = actual_attempt - planned_release`

TPOT 是一个请求首末 token 间的平均值，其分布 P99 不是单 token 间隔 P99。合并到同一事件的多个 token 共享客户端到达时刻，报告的是流式观测时延，不声称具有服务端逐 token 时间精度。

## Goodput

```text
cohort = 所有计划在 [0, duration) 释放的 ID
pass = 已实际尝试且完整完成
       且 TTFT <= 2 s（短输入）或 10 s（长输入）
       且 TPOT <= 50 ms
goodput = 达标的计划 ID 数 / duration
```

评估从 `cohort.jsonl` 左连接结果，而不是从成功文件开始。未尝试、拒绝、异常、超时、输出不完整和缺失日志均贡献 0。逐行结果用于异常终止后的恢复，正常结束还写包含未发项的完整快照。排空不延长 goodput 分母；另报含排空的实际完成吞吐，不将固定窗口 goodput 称为稳态最大容量。

发生器门槛：漏尝试=0、每次尝试早于窗口结束、无提前发送、释放迟滞 P99≤5 ms、max≤20 ms。未满足者标 `generator_limited`，保留诊断 goodput，不用于目标负载正式对比。服务错误仍计入失败，不能删除不利轮次。连接失败/transport error 单独保留其状态，发生器门槛本身不能断言故障一定来自客户端。

## 尾部与正确性

每组所有观察到首 token 的请求都进入 TTFT 条件分布，即使后来失败。未收到首 token 的数量、比例、已经观察到的等待下界单列，不把 timeout 值填成精确 TTFT。仅当整组都有首 token 才填 `all_request_ttft_p99_s`，否则保持空值。P99 使用 nearest-rank。

长输出完成率以全部计划长输出请求为分母；TPOT P50/P99 标明仅来自已完成请求。配对比较要求相同 trace 摘要，并比较双方共同完成请求的 token-ID SHA-256；共同完成之外的缺失/失败仍由完整 cohort 计入。摘要不一致需定位，不能仅凭时延改善忽略输出变化。

## 对照与采用条件

FCFS、无保护窗口、完整保护策略分别保存配置和结果。校准用独立种子，主回放使用另外三组种子并交替策略顺序；不把三次 P99 平均成总体 P99。每次 paired trace 同时要求 goodput 增加至少 5%、长输出完成率下降不超过 1 个百分点、长输出完成请求平均 TPOT 的 P99 增加不超过 2 ms。比较工具只判一组 trace，不自动声称多轮验收。

这些是留存方案中的工程取舍条件，不是行业通用标准。还应查看调度 CPU、抢占、重算工作、队列增长及负向对照；当前仓库没有已执行记录或已选出的调优赢家。
