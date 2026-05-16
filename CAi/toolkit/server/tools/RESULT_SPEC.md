# result.json 规范

> 所有工具脚本（`run.py`）写入的 `result.json` 必须遵守本规范。
> 不符合规范的输出会导致 Agent 无法正确解析结果。

---

## 基本规则

1. `result.json` 必须是合法的 UTF-8 编码 JSON 文件。
2. 顶层必须是一个 **对象**（`{}`），不能是数组或原始值。
3. 必须包含 `"success"` 字段（布尔值）。
4. **成功时不得包含 `"error"` 字段**（这是最常见的 bug 来源）。

---

## 成功时的结构

```json
{
  "success": true,
  "summary": {
    "task": "简要描述任务",
    "generated_count": 10,
    "processing_time_sec": 3.2
  },
  "results": {
    "...": "具体结果数据"
  }
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `success` | `bool` | ✅ | 必须为 `true` |
| `summary` | `object` | ✅ | 汇总统计，wrapper 优先读取 |
| `results` | `object` 或 `array` | ✅ | 完整的逐条结果 |

### ⚠️ 禁止事项（成功时）

```json
// ❌ 错误示范 — 成功时包含 "error" 字段
{
  "success": true,
  "summary": {...},
  "results": {...},
  "error": null          // ← 这会导致 Agent 误判为失败！
}

// ❌ 错误示范 — 成功时包含 "errors" 字段且值为 null
{
  "success": true,
  "results": {...},
  "errors": null         // ← 虽然目前不会触发 bug，但不推荐
}
```

### ✅ 正确示范

```json
{
  "success": true,
  "summary": {
    "task": "SCScore calculation",
    "total": 3,
    "successful": 3,
    "failed": 0,
    "avg_scscore": 2.15
  },
  "results": [
    {"smiles": "CCO", "scscore": 1.2},
    {"smiles": "c1ccccc1", "scscore": 2.5},
    {"smiles": "CC(=O)O", "scscore": 2.75}
  ]
}
```

---

## 失败时的结构

```json
{
  "success": false,
  "error": "具体的错误描述信息"
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `success` | `bool` | ✅ | 必须为 `false` |
| `error` | `string` | ✅ | 人类可读的错误描述 |

`"error"` 字段 **只在 `success: false` 时出现**。

---

## 部分失败的结构

当批量处理中部分条目失败时：

```json
{
  "success": true,
  "summary": {
    "total": 5,
    "successful": 3,
    "failed": 2
  },
  "results": [
    {"smiles": "CCO", "scscore": 1.2},
    {"smiles": "c1ccccc1", "scscore": 2.5},
    {"smiles": "CC(=O)O", "scscore": 2.75}
  ],
  "failed_items": [
    {"smiles": "INVALID", "reason": "RDKit cannot parse"},
    {"smiles": "X", "reason": "Empty molecule"}
  ]
}
```

注意：用 `"failed_items"` 而不是 `"error"` 或 `"errors"`。

---

## run.py 模板

```python
import json
import sys


def main():
    # 1. 读取参数
    params = json.load(open("params.json"))

    try:
        # 2. 执行计算
        output = do_computation(params)

        # 3. 写入成功结果（不包含 "error" 字段）
        result = {
            "success": True,
            "summary": {
                "task": "My Tool",
                "count": len(output),
            },
            "results": output,
        }

    except Exception as e:
        # 4. 写入失败结果
        print(f"Error: {e}", file=sys.stderr)
        result = {
            "success": False,
            "error": str(e),
        }

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
```

---

## 常见错误 & 排查

| 现象 | 原因 | 修复 |
|------|------|------|
| Agent 返回 `{"error": null}` | `result.json` 成功时包含了 `"error": null` | 删除该字段 |
| Agent 返回 `{"error": "Failed to parse string output"}` | `result.json` 不是合法 JSON | 检查编码和格式 |
| Agent 返回 `{"error": "Task finished but returned no data."}` | `result.json` 为空或未写入 | 确保 `main()` 正常执行到写入步骤 |
| Agent 返回 `{"error": "Tool execution failed: ..."}` | `success` 为 `false` | 检查工具脚本的异常处理 |

---

## 验证工具

提交任务后，可以直接检查沙盒目录中的 `result.json`：

```bash
# 找到最近的 job 目录
ls -lt CAi/toolkit/server/workspace/jobs/ | head -5

# 检查 result.json
cat CAi/toolkit/server/workspace/jobs/<uuid>/result.json | python -m json.tool

# 验证：成功时不应有 "error" key
python -c "
import json, sys
r = json.load(open(sys.argv[1]))
if r.get('success') and 'error' in r:
    print('❌ BUG: success=true but \"error\" key exists!')
    sys.exit(1)
print('✅ result.json is valid')
" CAi/toolkit/server/workspace/jobs/<uuid>/result.json
```

或使用项目自带的调试脚本：

```bash
python tests/debug_rxnflow.py
```
