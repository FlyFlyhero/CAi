# Code Execution Subsystem

`CAi/CAi_agent/execution/`

---

## 背景：为什么从 exec() 换成 Jupyter kernel

原始实现用 `exec()` + 模块级全局变量（`_PERSISTENT_NS`）实现持久 REPL，存在三个无法通过局部修补解决的结构性缺陷：

**1. 超时后并发执行**

`run_with_timeout` 基于 `ThreadPoolExecutor`。Python 线程无法被强制终止——`fut.cancel()` 对已经开始运行的 Future 是 no-op。超时后锁释放、新执行进来，两个线程同时在操作 `_PERSISTENT_NS` 和 `sys.stdout`。

**2. `sys.stdout` 替换不是线程安全的**

`sys.stdout` 是进程级全局变量。并发时，线程 A 的 `StringIO` 会被线程 B 的替换覆盖，B 恢复的 `old_stdout` 可能是 A 的 `StringIO`，导致输出丢失或混杂。

**3. 线程池耗尽**

每次超时都有一个僵尸线程留在 `_POOL`（`max_workers=8`）里继续跑。8 次叠加后线程池耗尽，新任务永久阻塞。

切换到 Jupyter kernel 后，Python 代码在独立子进程里运行：
- stdout/stderr 通过 ZeroMQ 消息返回，天然线程安全
- 超时发 SIGINT 到内核进程（`km.interrupt_kernel()`），无响应则 `km.restart_kernel()`
- 子进程可以真正被终止，不存在僵尸线程

---

## 模块结构

```
execution/
├── repl.py       # Jupyter kernel REPL（本文档的主要内容）
├── bash.py       # run_bash_script — subprocess wrapper，bash-explicit
├── timeout.py    # run_with_timeout — 仍用于 bash 执行路径
└── __init__.py   # 统一导出，接口不变
```

---

## repl.py 详解

### 模块级状态

```python
_km: KernelManager | None      # jupyter_client.KernelManager 单例
_kc: BlockingKernelClient | None  # 对应的 blocking client
_kernel_lock: threading.Lock   # 保护懒初始化，防止多线程同时启动 kernel
_WORKSPACE_DIR: str | None     # 工作目录（保存图片、设置 kernel cwd）
_CUSTOM_FNS_ATTR = "_base_CAi_custom_functions"  # builtins 上的工具注册表键名
```

整个模块维护一个 kernel 单例（与原来 `_PERSISTENT_NS` 单例的设计哲学一致）。单 agent 场景下，`base.py` 的 `_exec_lock` 保证串行执行，无竞争。

### 公开接口

所有接口与旧模块完全一致，`run_python_repl` 新增了可选的 `timeout` 参数：

```python
set_workspace_dir(path: str) -> None
run_python_repl(code: str, timeout: float = 600) -> str
inject_custom_functions(custom_functions: dict[str, Callable]) -> None
reset_namespace() -> None
```

### Kernel 生命周期

```
首次调用 run_python_repl / inject_custom_functions / reset_namespace
        │
        ▼
_get_or_start_kernel()          # 带 _kernel_lock 的懒初始化
        │
        ├─ _km is None or not _km.is_alive()  →  _start_kernel()
        │         │
        │         ├─ KernelManager(kernel_name="python3").start_kernel()
        │         ├─ km.blocking_client()
        │         ├─ kc.wait_for_ready(timeout=30)
        │         └─ _init_kernel_env(kc)          # Agg 后端 + CJK 字体 + os.chdir
        │
        └─ 注册 atexit._shutdown_kernel()（只注册一次）

正常退出 → _shutdown_kernel()
        ├─ kc.stop_channels()
        └─ km.shutdown_kernel(now=True)
```

`_init_kernel_env` 在 kernel 启动或重启后立即执行，完成：
- `matplotlib.use('Agg')` — 强制非交互式后端，防止 `plt.show()` 阻塞
- CJK 字体探测 — 遍历候选字体列表，把第一个已安装的字体插到 `font.sans-serif` 开头
- `os.chdir(_WORKSPACE_DIR)` — 如果工作目录已配置

### 消息收集循环（`_execute_in_kernel`）

Jupyter wire protocol 的消息流如下：

```
Client                          Kernel
  │── execute_request ──────────►│
  │                               │── status: busy ──────────►│ (iopub)
  │                               │── execute_input ──────────►│ (iopub)
  │                               │── stream (stdout) ─────────►│ (iopub, 可多条)
  │                               │── execute_result / error ──►│ (iopub)
  │                               │── status: idle ────────────►│ (iopub)
  │◄── execute_reply ─────────────│ (shell)
```

实现只监听 iopub channel，以 `status: idle` 作为结束信号：

```python
while True:
    remaining = deadline - time.monotonic()
    if remaining <= 0 and not timed_out:
        timed_out = True
        _handle_timeout(kc, timeout)   # SIGINT → 可选 restart
        remaining = 3.0                # 再等 3s 收尾消息

    msg = kc.get_iopub_msg(timeout=min(remaining, 1.0))  # 最多等 1s/次

    match msg['msg_type']:
        'stream'         → 追加到 stdout_parts
        'execute_result' → 取 text/plain，追加到 stdout_parts
        'error'          → 拼 traceback，存入 error_text（ANSI 转义符剥离）
        'status: idle'   → break
```

`execute_result` 消息对应 IPython 的"单元格最后一行表达式的 repr"——这是相对旧 `exec()` 行为的有意改变：LLM 写 `df.shape` 不加 `print()` 也能看到结果。

**超时处理**（`_handle_timeout`）：

```
超时
 ├─ km.interrupt_kernel()      # 发 SIGINT
 ├─ kc.kernel_info(timeout=5)  # 探活 ping
 │     ├─ 成功 → 继续收尾消息
 │     └─ 失败（超时）→ _restart_kernel()
 │               ├─ km.restart_kernel(now=True)
 │               ├─ 新 blocking_client，wait_for_ready
 │               └─ _init_kernel_env(new_kc)
 └─ 返回 "TIMEOUT: ..." 字符串
```

### 工具注入（`inject_custom_functions`）

原方案直接把 Python 对象写入 `_PERSISTENT_NS` dict，跨进程后无效。新方案用 `cloudpickle` 序列化：

```python
payload = base64.b64encode(cloudpickle.dumps(funcs)).decode()
inject_code = """
import cloudpickle as _cp, base64 as _b64
globals().update(_cp.loads(_b64.b64decode(<payload>)))
del _cp, _b64
"""
_execute_in_kernel(kc, inject_code, timeout=15)
```

`cloudpickle`（pip: `cloudpickle>=3.0`）能序列化闭包、局部定义的函数、lambda 等标准 `pickle` 无法处理的对象，覆盖了测试中注入局部闭包的场景。

同时，`inject_custom_functions` 仍然更新 `builtins._base_CAi_custom_functions`，保持与 `ReplBridge` 的契约不变。

**`_sync_builtins_to_kernel`** 在每次 `run_python_repl` 开始时调用，把 `builtins._base_CAi_custom_functions` 里的全部函数注入到 kernel。这样 `ReplBridge.sync()` 直接写 builtins 的路径（测试用）也能工作。

### 图片捕获

图片捕获逻辑分两层，逻辑与旧模块完全一致：

1. **matplotlib 图** — 执行完成后，向 kernel 发送一段代码，遍历 `plt.get_fignums()`，把每张图保存为 `plot_<timestamp>_<i>.png`，`print` 路径列表后由 host 侧读取 stdout。

2. **其他库生成的图片**（RDKit、Pillow、plotly 等）— 执行前后各做一次工作目录的文件快照（`_snapshot_workspace_images` / `_detect_new_images`），差集即为新生成的图片。

所有检测到的图片路径以 `[Image saved]: /path/to/file.png` 形式追加到输出末尾，由 web UI 的前端解析并渲染。

### `reset_namespace`

调用 IPython magic `%reset -f`，清空用户命名空间（所有用户定义的变量、函数、导入）。不重启 kernel 进程，耗时约 50ms，适合在测试的 autouse fixture 里频繁调用。

`%reset -f` 不会清除 IPython 内部状态（如历史记录索引），也不会影响已安装的 C 扩展的全局状态（如 RDKit 的分子缓存）。如需完全干净的状态，调用 `_restart_kernel()` 重启进程（更慢，约 1-2s）。

---

## base.py 侧的调用方式

Python 执行路径去掉了 `run_with_timeout` 包装，timeout 直接传入 `run_python_repl`：

```python
# bash（保持不变）
result = run_with_timeout(run_bash_script, [script], timeout=self.timeout_seconds)

# python（新）
result = run_python_repl(code, timeout=self.timeout_seconds)
```

原因：旧的 `run_with_timeout` 通过 `ThreadPoolExecutor` 实现超时，对 Python 路径已无意义（kernel 自己处理了）；对 bash 路径仍然有效（subprocess 也无法从外部强杀，但 `run_with_timeout` 至少让调用方不阻塞）。

---

## 依赖

| 包 | 用途 | 版本要求 |
|----|------|----------|
| `jupyter_client` | `KernelManager` + `BlockingKernelClient` + ZeroMQ 消息协议 | `>=8.0` |
| `ipykernel` | 实际的 IPython kernel 进程（kernel_name="python3" 指向它） | `>=6.0` |
| `cloudpickle` | 序列化任意 Python callable（含闭包）跨进程注入 | `>=3.0` |

在典型的科学 Python 环境（conda + jupyter）下这三个包通常已经存在。

---

## 局限性与已知约束

**共享 kernel 单例**：模块级单例意味着同进程内的两个 `A1pro` 实例共享同一个 kernel（与旧的 `_PERSISTENT_NS` 行为一致）。需要进程级隔离时，可以将 kernel 管理改为 per-instance 类（改动范围：`repl.py` + `base.py` 构造函数）。

**`_sync_builtins_to_kernel` 每次都重新注入全量工具**：在 10-20 个 toolkit 函数的规模下 cloudpickle 序列化开销可忽略（< 1ms），但若工具数量增长到数百个，可以引入版本号或 hash 做增量注入。

**`set -e` 对 bash 的检测仍是简单字符串匹配**：见 `bash.py`，本次未改动。
