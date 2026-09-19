# 调度与容量提交

## 一轮调度

运行请求沿原路径调度，包括增页、抢占与分块。还有 token/seq 预算时尝试等待队首；成功则继续正常 FCFS，只有符合支持契约的 KV 容量拒绝才启动窗口。

`AdmissionRound` 在 `schedule()` 开始创建，成功不会创建新轮次。快照使用 `islice(queue, 1, W + 1)`，只保留请求引用，不保留块 ID。检查、失败或跳过都消耗一个原位置；成功不会把第 9 个位置补进来。候选用尽或遇到屏障便结束本轮等待准入。

## 容量检查与提交

`peek_computed_blocks()` 沿用原连续前缀查询，分离缓存事件发布。在单组 dense full-attention、页粒度哈希的范围内，查询不改页引用、空闲链、缓存映射或正式命中统计。

同一 scheduler owner 紧接着调用原 `allocate_slots()`，统一处理完整已知序列、本步新增槽位、可驱逐命中页重新引用和原 watermark。这里的完整序列包含抢占恢复时已经生成的输出历史，不读取未来输出。

本项目没有独立页数 probe，也不保存上次“能放下”的结果。支持范围内 `remove_skipped_blocks()` 不释放页，普通 `None` 拒绝可继续看下一候选；此契约不推广到滑窗或混合缓存。分配异常直接沿引擎错误路径传播，不捕获后继续，不伪造回滚。

成功分配后才发布候选缓存事件、更新 `shared_prefix_boundary` 与 Prefill 统计，调用原命中统计入口，移除等待项、更新 running 状态，再增加绕行计数。下一候选从当前页池重新查前缀，避免使用前一提交驱逐前的旧块 ID。

## 绕行保护与生命周期

`predecessors` 只包含本次扫描中失败/跳过且仍等待的前驱，成功请求不加入。接纳后继时，列表中每个前驱累计一次；屏障判断只看前驱，不看候选自身的阈值。

`AdmissionPolicy.bypass_counts` 按请求生命周期维护。`_preempt_request()` 保留累计值，进入 running 时也不清零；`_free_request()` 在完成或取消时删除，避免请求 ID 复用继承旧值。

同一 owner 在 `schedule()` 内不处理异步取消回调。失败条目无需临时弹出，因此拒绝不会改变真实队列顺序。

## 支持与回退

`unsupported_reasons()` 检查同步、FCFS、单 GPU、单 KV group、精确 full-attention spec/manager 类型、页粒度哈希。connector、投机、LoRA、多模态、encoder-decoder、pooling、diffusion 等不启用新策略。出现 skipped waiting 或本步 Prefill 节流时也不启动窗口。

结构化输出、流式输入、在途 token 等不适合的候选消耗位置、原位保留。关闭分块 Prefill 时，候选自身超过当前 token 预算则跳过；普通队首保留上游停止分支。全局 token/seq 不足始终结束扫描。

## 开销与边界

W 限制查询数量，不表示完整调度为 O(W)。前驱计数可能 O(W²)，上游 `deque.remove()` 可能 O(N)，前缀查询还有输入长度成本。`metrics.py` 分别累计快照、屏障/游标、查询、分配、队列提交和计数的主机耗时，不据此声称已有净收益。

Decode 增长、抢占重算与最大长度检查保留上游。`scheduled_recompute_tokens` 统计抢占前计算边界以内再次被调度的 token，排除重新命中的前缀；它是计划执行的工作量，不是实测 GPU 时间。B 限制成功绕行次数，不保证等待秒数。
