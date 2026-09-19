# vLLM KV Cache 准入调度与队首阻塞优化

在 vLLM V1 等待队列中实现受 KV 容量和公平性约束的有限窗口准入。队首因 KV 不足无法进入时，按原队列顺序检查后续最多 **8 个位置**；任一等待前驱累计被成功越过 **8 次**后，阻挡后继继续插队。

**这是依据留存设计资料重新实现的源码工程。当前未编译、未运行、未测量性能。** 历史代码与原始实验产物未恢复，简历中的历史性能数据不作为此版本的验证结果。

## 核心实现

| 模块 | 实现入口 | 行为 |
|---|---|---|
| KV 容量检查 | [kv_cache_manager.py](vllm/v1/core/kv_cache_manager.py) | 抽出不发布事件的查询；直接调用原分配器，沿用可驱逐命中页计费、完整已知序列检查和 watermark |
| 有限窗口 | [scheduler.py](vllm/v1/core/sched/scheduler.py)、[policy.py](vllm/v1/core/sched/kv_admission/policy.py) | 只从 KV 拒绝启动，截取 W 个原位置；成功不补位，各候选重新查页，保留 Prefill 约束 |
| 等待保护 | [policy.py](vllm/v1/core/sched/kv_admission/policy.py) | 成功后计数全部等待前驱；达到 B 形成屏障；抢占保留，完成或取消清理 |
| 支持与回退 | [compat.py](vllm/v1/core/sched/kv_admission/compat.py) | 同步、单 GPU、单组 dense full-attention、FCFS；其他模式沿原调度路径 |
| 回放与统计 | [benchmarks/](benchmarks/) | 开放到达、流式 token ID 计数、完整 cohort goodput、分组尾延迟、发送迟滞、输出摘要对照 |

## 工程组织

```text
vllm-kv-admission/
├── vllm/v1/core/                 # 与固定上游同路径的源码覆盖层
│   ├── kv_cache_manager.py      # 前缀查询与事件发布分离
│   └── sched/
│       ├── scheduler.py         # 真实引擎接入与请求生命周期
│       └── kv_admission/        # 配置、兼容性、窗口/公平性、主机计数
├── benchmarks/                  # 生成计划、SSE 回放、评估、配对比较
├── configs/                     # 引擎起始配置与校准/消融参数空间
├── tools/                       # 上游源码组装、启动命令、调度日志提取
├── docs/                        # 设计、简历对应、接入、统计、状态推演
├── upstream.lock                # 固定 commit 与原始覆盖文件 SHA-256
├── THIRD_PARTY_NOTICES.md        # 直接复用与设计参考分开标注
└── LICENSE
```

本仓库采用固定上游源码覆盖层，不另写模型执行或第二套页分配器。完整引擎来自 [`vLLM@fa008bd`](https://github.com/vllm-project/vllm/tree/fa008bdccf10f2f31f84553112a227cf1b8947d7)，`tools/prepare_upstream.py` 可将其组装到独立目录。顶层 `vllm/` 不是可单独安装的发行包。

导入上游原文件的提交为 [`981e20c`](https://github.com/StrongObserver/-vLLM-KV-Cache-/commit/981e20c23cc76784c7799731e419b80aa05a6cca)。使用 `git diff 981e20c -- vllm/` 可直接区分上游代码与新增修改。

## 三种策略

| `KV_ADMISSION_MODE` | 等待队列选择 | 公平性 |
|---|---|---|
| `fcfs`（默认） | 原 FCFS | 不启用窗口 |
| `window` | 最多 W 个后续原位置 | 无绕行次数屏障，用于消融 |
| `bounded` | 同一窗口机制 | 每个前驱累计 B 次后阻挡后继 |

`KV_ADMISSION_WINDOW=8`、`KV_ADMISSION_BYPASS_LIMIT=8` 对应留存设计。它们不保证等待秒数，也不读取未来输出长度。配置中的 watermark、token budget 等起始值未恢复为历史调优值。

## 阅读顺序

1. [简历条目与代码对应](docs/resume-map.md)：从三条负责模块定位实现。
2. [调度设计](docs/design.md)：提交顺序、窗口、屏障与回退。
3. [状态推演](docs/review-scenarios.md)：容量、驱逐、抢占与取消的手工审阅输入。
4. [统计协议](docs/measurement.md)：goodput、尾部缺失与消融。
5. [接入说明](docs/integration.md)、[还原状态](docs/reconstruction-status.md)：源码组装和验证边界。

上游源文件保留 Apache-2.0 版权头；新增实现同样采用 Apache-2.0。借鉴范围见 [来源说明](THIRD_PARTY_NOTICES.md)。
