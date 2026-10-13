# 数据格式

## Issue CSV

文件必须是 UTF-8（允许 UTF-8 BOM），扩展名必须是 `.csv`。

| 列 | 必需 | 含义 |
| --- | --- | --- |
| `Issue_KEY` | 是 | 项目内唯一 Issue 标识 |
| `Issue_Text` | 是 | 标题与描述组成的可检索文本 |
| `creationdate` | 是 | ISO-8601 创建时间 |
| `resolutiondate` | 否 | ISO-8601 解决时间；空值表示未解决 |

最小示例：

```csv
Issue_KEY,Issue_Text,creationdate,resolutiondate
PROJ-101,"Parser fails for encrypted documents",2025-01-01T09:00:00Z,2025-01-10T18:00:00Z
```

## Commit CSV

| 列 | 必需 | 含义 |
| --- | --- | --- |
| `Commit_SHA` | 是 | 项目内唯一 Commit 标识或完整 SHA |
| `Commit_Text` | 是 | Commit message |
| `Commit_Code` | 是 | diff、变更摘要或预处理后的代码文本；允许空字符串 |
| `commitdate` | 是 | ISO-8601 提交时间 |

最小示例：

```csv
Commit_SHA,Commit_Text,Commit_Code,commitdate
a101f00,"PROJ-101 fix encrypted parser","parser.setPassword(password);",2025-01-08T15:00:00Z
```

## 日期与校验规则

- 推荐显式提供时区，例如 `2025-01-01T09:00:00Z` 或 `2025-01-01T17:00:00+08:00`。
- 没有时区的值按 UTC 处理；服务端统一存为 UTC。
- 同一文件中不允许重复 `Issue_KEY` 或 `Commit_SHA`。
- 必填标识和文本去除首尾空白后不能为空。
- 空文件、缺列、非 UTF-8、非 ISO-8601 日期和超过上限的文件会返回 `422 validation_error`。
- 默认单文件上限是 20 MiB，可通过 `ACCSTRACE_MAX_UPLOAD_BYTES` 修改。

每次双文件导入生成一个 dataset revision。服务端保存原始文件、行数、schema version 和各自 SHA-256，后续 run 固定引用该 revision。

## 项目键与 Issue key 前缀

项目键用于选择对应项目训练得到的 ACCS 模型。常规格式为大写项目标识，例如 `TIKA`；组合域可以写成 `HADOOP/MAPREDUCE`。

- 若项目尚未配置键，导入时会从所有 `Issue_KEY` 的第一个 `-` 前推断并持久化，例如 `TIKA-101` 推断为 `TIKA`。
- 若项目已有键，所有观察到的 Issue 前缀必须属于该键，否则整个导入失败且不会建立 revision。
- 无法安全推断的旧格式仍可用于 demo，但真实模型运行会被阻止，直到用户在第一次 run 前配置合法项目键。
- 第一次 run 后项目键锁定，避免同一数据版本被静默切换到其他项目模型。

项目键匹配只能防止明显的跨项目误用，不能代替跨版本漂移评估或跨项目泛化实验。

## 候选与复核字段

候选列表返回：

| 字段 | 含义 |
| --- | --- |
| `raw_score` | Runtime 的原始概率样分数，不自动视为校准概率 |
| `calibrated_probability` | 仅在部署校准已验证时存在，否则为 `null` |
| `reliability_level` | `recommended`、`review` 或 `rejected` |
| `predicted_link` | 决策分数是否达到低阈值 |
| `time_delta_hours` | Commit 时间减 Issue 创建时间 |
| `review_status` | `unreviewed`、`accepted`、`rejected` 或 `skipped` |
| `review_version` | 乐观锁版本，提交复核时作为 `expected_version` 回传 |

详情响应还包括完整 Issue 文本、Commit 文本、代码文本、日期、决定历史、模型来源和阈值。

## 导出字段

CSV 和 JSON 使用相同逻辑字段：

```text
Issue_KEY
Commit_SHA
raw_score
calibrated_probability
reliability_level
predicted_link
review_status
reviewer_decision
reviewer_note
model_name
model_version
run_id
```

`reviewer_decision` 与 `reviewer_note` 是候选的最新一条复核记录；完整历史保留在 SQLite 中，但不包含在当前导出格式内。
