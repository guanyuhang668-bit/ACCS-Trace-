# HTTP API

启动 API 后，交互式 OpenAPI 位于 <http://127.0.0.1:8000/docs>。所有业务端点以 `/api` 开头，错误使用统一结构：

```json
{
  "error": {
    "code": "validation_error",
    "message": "Human-readable message",
    "details": null
  }
}
```

## 已实现端点

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/health` | 检查 API 版本与数据库连接 |
| `POST` | `/api/projects` | 创建项目 |
| `GET` | `/api/projects` | 列出项目及 revision/run 数量 |
| `GET` | `/api/projects/{project_id}` | 获取项目与 dataset revisions |
| `PATCH` | `/api/projects/{project_id}` | 持久化项目键或描述；第一次 run 后项目键锁定 |
| `POST` | `/api/projects/{project_id}/imports` | 以 multipart 上传 `issues_file` 和 `commits_file` |
| `GET` | `/api/models` | 列出可用 model releases 与 calibration profiles |
| `POST` | `/api/models/register` | 校验并注册本地 models 目录中的一个 TorchScript 包 |
| `POST` | `/api/runs` | 创建 queued Recover run |
| `GET` | `/api/runs` | 列出 run，可按 `project_id` 过滤 |
| `GET` | `/api/runs/{run_id}` | 获取 run 进度、配置、汇总或错误 |
| `GET` | `/api/runs/{run_id}/manifest` | 获取 schema 版本、数据 hash、模型、校准、配置和结果复现清单 |
| `GET` | `/api/runs/{run_id}/events` | 获取按时间排序的审计事件，`limit` 为 `1..500` |
| `POST` | `/api/runs/{run_id}/cancel` | 请求取消未结束 run |
| `POST` | `/api/runs/{run_id}/retry` | 将 `failed` 或 `interrupted` run 清理状态后重新排队 |
| `GET` | `/api/runs/{run_id}/candidates` | 分页筛选候选 |
| `GET` | `/api/candidates/{candidate_id}` | 获取候选详情和复核历史 |
| `POST` | `/api/candidates/{candidate_id}/reviews` | 追加人工决定，带乐观锁版本 |
| `GET` | `/api/runs/{run_id}/analytics` | 获取类别、复核计数和 10 桶原始分数直方图 |
| `POST` | `/api/runs/{run_id}/exports` | 为 completed run 创建 CSV/JSON 导出 |
| `GET` | `/api/exports/{export_id}/download` | 下载已生成导出 |

校准 profile 注册/拟合、Adapt 和 Evaluate 端点尚未实现。

## 注册本地模型包

`POST /api/models/register` 只接收一个单层安全目录名：

```json
{
  "package_name": "accs-paper-v1"
}
```

对应目录必须已经存在于 `ACCSTRACE_DATA_DIR/models/accs-paper-v1/`，并包含符合 `accs_torchscript_v1` 的 `manifest.json`。API 不接受客户端绝对路径、不上传或解压模型。注册前会校验 manifest、三个制品 SHA-256、项目范围声明，并在 CPU 上加载 TorchScript 执行张量契约探针；任何一步失败都不会持久化 release。真实模型不能使用 `supported_project_keys=["*"]`。成功返回 `201`，重复的模型名称与版本返回 `409`。该端点不创建 calibration profile，模型包细节见 [模型包与运行时](model-package.md)。

`GET /api/models` 除包路径与校验和外，还返回 `model_scope`、`project_key_strategy` 和 `supported_project_keys`。客户端据此隐藏不兼容的真实模型；`POST /api/runs` 与 worker 是最终权威校验，不能只依赖前端过滤。

## 项目键

创建项目时可提供 `project_key`。项目键会转成大写，允许用 `/` 组合多个项目标识，例如 `HADOOP/MAPREDUCE`。未提供时，数据导入会尝试从 `<PROJECT>-<ID>` 形式的 `Issue_KEY` 推断；显式项目键与导入前缀不一致时返回 `422`。

在第一次 run 创建前，可持久化修改项目键：

```json
{
  "project_key": "TIKA"
}
```

第一次 run 后变更项目键返回 `409 conflict`，避免既有 run manifest 和模型适用域被改写。内置 demo 是兼容旧项目的唯一例外；非 demo 模型要求项目键和有效范围声明。

## Run manifest 与审计

`GET /api/runs/{run_id}/manifest` 返回 `manifest_schema_version=1.0`，并固定记录：

- project ID/name/project key；
- dataset revision ID、schema version、行数和两个输入 SHA-256；
- model release ID、名称、版本、runtime kind、可选 package SHA-256、来源域、demo 标志、项目范围和公开 metadata；
- 可选 deployment calibration profile、是否验证、温度、阈值、来源域和指标；
- run config、summary 及创建/开始/完成时间。

响应不会暴露服务端 `model_path`。`GET /api/runs/{run_id}/events` 返回 `event_type`、message、payload 和时间，用于追踪 queued、claimed、stage、completed、reviewed、exported、cancelled、interrupted 和 retried 等实际发生的事件。

`POST /api/runs/{run_id}/retry` 只接受 `failed` 或 `interrupted`，并复用同一个 run ID、dataset revision、model release 和 config。completed、cancelled、queued 或 active run 会返回 `422 validation_error`。

## 创建 run

```json
{
  "project_id": "PROJECT_UUID",
  "dataset_revision_id": "REVISION_UUID",
  "model_release_id": "MODEL_UUID",
  "calibration_profile_id": null,
  "mode": "recover",
  "days_before": 3,
  "days_after": 14,
  "unresolved_window_days": 90,
  "max_candidates_per_issue": 200,
  "threshold_low": 0.3,
  "threshold_high": 0.75
}
```

`threshold_low` 必须小于 `threshold_high`。两者省略时使用所选 profile 的阈值；没有 profile 时后端回退为 `0.40` 和 `0.75`。当前内置 demo model 自带一个未验证 profile，其低阈值为 `0.30`。

创建 run 时，dataset revision 必须属于所选项目，真实 model release 必须声明与项目键兼容的范围。该判断同时写入 `config_json`，包括 `project_key`、`model_scope`、`project_key_strategy` 和 `supported_project_keys`。

## 候选分页与筛选

查询参数：

- `reliability_level`: `recommended`、`review`、`rejected`；
- `review_status`: `unreviewed`、`accepted`、`rejected`、`skipped`；
- `search`: 匹配 Issue key 或 Commit SHA；
- `limit`: `1..200`，默认 50；
- `offset`: 非负整数。

## 追加复核决定

```json
{
  "decision": "accepted",
  "note": "Verified from the commit message and code change.",
  "reviewer": "local-user",
  "expected_version": 0
}
```

客户端必须使用候选当前的 `review_version`。版本过期时 API 返回 `409 conflict`，客户端应刷新详情，而不是覆盖新决定。

## 创建导出

```json
{
  "format": "csv",
  "include_rejected": true
}
```

`format` 仅接受 `csv` 或 `json`，且 run 必须为 `completed`。
