# 简历条目 → 代码

对应当前简历中该项目的三条负责模块。以下是实现对应关系，不是运行验证结论。

| 简历中的功能/逻辑 | 具体入口 | 本次还原方式 |
|---|---|---|
| 复用页需求、计入空闲命中重新引用 | `kv_cache_manager.py:allocate_slots` → 上游 `get_num_blocks_to_allocate` | 保留原需求计算与 `ref_cnt==0` 命中页计费 |
| 同一流程内检查与分配 | `scheduler.py:Scheduler.schedule` 的 `waiting_allocate` 段 | 新鲜查询后立即调用原分配器一次 |
| 拒绝不改队列/页引用 | `peek_computed_blocks`；`AdmissionRound.reject_capacity` | 不发布查询事件，失败只改变私有游标，不弹出等待项 |
| 每次成功后重新查询 | `Scheduler.schedule` 的 `admission_query` 分支 | 每次循环重新查前缀，策略不保存块 ID |
| 每轮最多 8 个后续位置 | `AdmissionRound.reject_capacity/next_candidate` | 固定原位置快照、整轮预算、成功不补位 |
| 只在 KV 不足时启动 | `new_blocks is None` 分支 | token、运行名额、请求状态与支持范围仍控制入口 |
| 按序准入 | `next_candidate/remove_committed` | 按快照顺序，成功才用原队列接口移除 |
| watermark / 分块 Prefill | 原分配器参数与调度 token 限制 | 不改水位线公式，保留 long-prefill threshold 等 |
| 所有被成功越过的前驱累计 | `AdmissionRound.on_admitted` | 每次成功给全部仍等待前驱各加 1 |
| 达到 8 次形成屏障 | `AdmissionRound.next_candidate` | 任一前驱达到 B 则停止；候选自身可准入 |
| 抢占重入不清零 | `_preempt_request` → `AdmissionPolicy.on_preempt` | 保留累计值；运行期间也不清零 |
| 完成/取消清理 | `_free_request` → `AdmissionPolicy.on_finish` | 最终释放时删除自身记录 |
| goodput 双预算 | `benchmarks/evaluate.py:summarize` | 完整 cohort；完成且 TTFT/TPOT 同时达标才计入 |
| 长输入 P99 与长输出代价 | `evaluate.py`、`compare.py` | 分组、nearest-rank、无首 token 明示、完成率与 TPOT |

新增部分是窗口、公平性、生命周期接入、候选查询事件隔离、诊断和回放/报告。页管理、前缀缓存、模型执行、原队列与抢占机制仍复用 vLLM。

留存材料中的 3.8→4.2 请求/s、4.4→4.2 请求/s、13.6→8.0 s 属于历史描述；原始 trace 和逐请求记录未恢复，不写入本次 `summary.json`，不生成替代性实测数据。
