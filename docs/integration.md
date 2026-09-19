# 固定版本接入

## 源码组织

本仓库只维护两份修改后的上游文件和新增策略模块。源码组装工具在**新目录**拉取 `upstream.lock` 固定提交，核对覆盖文件的原始 SHA-256，再复制覆盖层，并记录每份最终源码的摘要。它不安装依赖、不编译、不启动引擎；已有目录不会被覆盖。

```bash
python3 tools/prepare_upstream.py --target .worktrees/vllm
```

此命令为后续接入入口，本次还原未执行。引擎运行环境、CUDA 依赖和可用的 vLLM 二进制需与固定源码兼容；仅复制 Python 源码不能替代完整运行环境。

## 配置

`configs/engine.toml` 对应单卡 Qwen2.5-7B BF16、16 GiB KV、block=16、max length=16384、max seq=128、同步调度、前缀缓存和分块 Prefill。`watermark=0`、token budget=2048、长 Prefill threshold=512 是还原起始配置，**未识别为历史调优值**。Graph 与注意力后端沿固定版本环境的默认选择，正式比较前应记录并在两方案间固定。

`configs/experiment_matrix.toml` 描述独立校准范围和消融协议，不是“已执行实验”清单。先分别调优 FCFS 与新策略，再冻结主对照；消融只改变窗口/公平性，保持其他参数一致。

以下命令只输出启动命令并保存清单，需要明确的模型 commit。脚本使用 Python 3.11+ 标准库：

```bash
python3 tools/launch_server.py \
  --source-root .worktrees/vllm \
  --config configs/engine.toml \
  --model-revision MODEL_COMMIT \
  --mode bounded \
  --manifest artifacts/bounded-engine.json
```

将 `bounded` 改为 `fcfs` 或 `window` 可得到另外两种策略。只有额外传入 `--execute` 才启动服务；本次未启动。`MODEL_COMMIT` 必须替换为实际模型和 tokenizer 的冻结版本。

## 接入点

1. `Scheduler.__init__` 创建策略并检查支持范围；不支持时记录原因、保持原调度。
2. `Scheduler.schedule` 创建单轮窗口预算，保留 running 路径，从 waiting 的 KV 拒绝分支启动扫描。
3. `KVCacheManager.peek_computed_blocks` 分离事件副作用；原 `get_computed_blocks` 通过 wrapper 保留原对外行为。
4. `_preempt_request` 记录抢占但保留公平性；`_free_request` 处理最终清理。
5. `_update_after_schedule` 记录再次调度的重算 token；模型执行输出仍由原引擎处理。

## 回退与日志

环境变量 `KV_ADMISSION_MODE=fcfs` 关闭窗口。源码回退应使用原始固定 checkout，勿在有本地改动的目录强行 reset。组装工具不会自动删除失败的中间目录。

`KV_ADMISSION_LOG_INTERVAL=1000` 每 1000 个调度步输出一次累计 JSON 计数；设为 0 关闭输出。三种策略使用相同采样间隔。计时本身有开销，回放时同样纳入端到端延迟。

```bash
python3 tools/summarize_scheduler_log.py artifacts/server.log
```

提取器计算两个累计样本之差，不对累计快照求和。采样边界需另与客户端 cohort 对齐；不能把任意服务器区间计数直接填成某轮精确资源代价。
