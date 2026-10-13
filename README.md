# ACCS-Trace

ACCS-Trace 是一个本地优先的 Issue-Commit 跟踪链接恢复与可靠性审查工具。它导入项目的 Issue 与 Commit，按时间窗口生成候选对，调用冻结的评分模型，为候选链接分级，并把不确定结果交给人工复核，最终导出可追踪的 CSV 或 JSON。

本仓库采用 **ACCS-only** 定位，不组合其他图传播或多算法融合方案。ACCS 是模型训练方法；ACCS-Trace 是消费训练产物的部署与审查工具，两者不能混为同一个算法阶段。

> **当前模型状态：**仓库已经实现 `accs_torchscript_v1` 的离线 CPU 推理适配器和受控注册 API，但没有附带可用的 ACCS 任务 checkpoint。内置的 `Deterministic Demo Runtime` 只是确定性的启发式评分器，用于开发、端到端测试和界面演示；它不是 ACCS 模型，不能产生论文实验结论。真实模型转换与接入边界见 [模型包与运行时](docs/model-package.md)。

## 已实现范围

- 创建本地项目并导入两个 UTF-8 CSV 数据文件；
- 持久化或从 `Issue_KEY` 推断项目键，只显示与项目域兼容的模型，并在 API/worker 端再次校验；
- 校验字段、日期、空行、重复 Issue key / Commit SHA、扩展名和上传大小；
- 按 Issue 生命周期与可配置时间窗口生成候选 Issue-Commit 对；
- 通过独立 worker 执行确定性 demo 推理，或调用已注册的离线 CPU TorchScript 模型包；
- 按双阈值划分 `recommended`、`review`、`rejected`；
- 查询、筛选、搜索候选，查看 Issue、Commit、代码片段和模型来源；
- 以乐观锁保存 `accepted`、`rejected`、`skipped` 人工决定及历史记录；
- 查看结果计数与原始分数直方图；
- 导出 CSV / JSON，并记录文件 SHA-256、模型版本和 run ID；
- 查询每个 run 的复现 manifest 与按时间排序的审计事件；
- 将 `failed` 或 `interrupted` run 原地重置并重新排队；
- 校验并注册 `ACCSTRACE_DATA_DIR/models/` 下的本地 `accs_torchscript_v1` 模型包；
- 使用 SQLite 保存项目、数据版本、运行、候选、复核、导出和审计事件；原始导入与导出文件保存在本地 `data/`。

目前只实现 Recover 工作流。Adapt、训练和部署校准 profile 的拟合/注册尚未实现。真实模型推理代码已经实现，但仓库没有发布真实 checkpoint，因此不能把 demo 结果写成 ACCS 方法结果。

## 架构

```text
React/Vite web client (127.0.0.1:5173)
                    |
                    | HTTP /api
                    v
FastAPI API (127.0.0.1:8000) ---- SQLite (WAL)
                    |                  |
                    | queued runs      +-- metadata, reviews, audit
                    v
Python worker ---------------------- local files
  candidate generation               imports, exports, model package*
  runtime scoring
  reliability triage

* Real ACCS model package is not included in this repository.
```

API 与 worker 分离，避免长时间推理阻塞交互请求。SQLite 是必要组件：人工复核版本、运行状态和审计记录都需要事务持久化；大文件不放入数据库。详细设计见 [系统架构](docs/architecture.md)。

## 环境要求

- Windows 10/11 与 PowerShell 7（初始化和演示脚本）；
- Python `3.11.x`；
- Node.js `20.19+` 或 `22.12+` 与 npm；
- 仅在首次安装依赖时需要网络。应用运行本身不下载模型或上传项目数据。

Python 的基础运行、测试、传递依赖和构建工具精确版本记录在 `requirements-lock.txt`。Windows x86-64 的可选 CPU TorchScript 依赖使用带 distribution hashes 的 `requirements-model-runtime-win-x64-lock.txt`；两者都不包含 editable 项目或本机路径。`frontend/package-lock.json` 锁定前端依赖。

## 一键初始化与开发启动

在仓库根目录执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
.\scripts\dev.ps1
```

默认安装保持轻量，只运行 demo。需要转换、验证或运行真实模型包时显式执行：

```powershell
.\scripts\bootstrap.ps1 -WithModelRuntime
```

只需重放论文表格提取或重新生成保留结果图时执行：

```powershell
.\scripts\bootstrap.ps1 -WithPaperTools
```

两个可选环境可以同时安装：

```powershell
.\scripts\bootstrap.ps1 -WithModelRuntime -WithPaperTools
```

然后访问：

- Web：<http://127.0.0.1:5173>
- OpenAPI：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

`dev.ps1` 同时启动 API、worker 和 Vite，日志写入 `data/logs/`；按 `Ctrl+C` 后脚本回收这三个子进程。它不会把服务绑定到局域网地址。

## Synthetic demo

仓库提供 [issues.csv](sample_data/issues.csv) 和 [commits.csv](sample_data/commits.csv)。启动服务后，可在 Web 中创建项目并上传这两个文件，也可运行完整 API 演示：

```powershell
.\scripts\demo-api.ps1
```

脚本会创建一个带时间戳的 demo 项目、导入样例、选择内置 demo runtime、等待 worker 完成并下载 CSV 到 `data/demo-exports/`。这些结果只能验证工作流和软件行为。

输入列、时间语义和输出列见 [数据格式](docs/data-format.md)，端点说明见 [API](docs/api.md)。

## 真实模型包

旧训练代码保存的 `torch.save(model)` checkpoint 必须先在受信任、隔离的训练环境中转换；pickle 加载可能执行任意代码，部署服务不会直接读取它。转换器生成双输入 raw-logits TorchScript、离线 tokenizer/preprocessing 和 SHA-256 清单：

```powershell
.\.venv\Scripts\python.exe .\scripts\export-legacy-accs-package.py --help
```

把生成目录放在 `ACCSTRACE_DATA_DIR/models/<package_name>/` 后，通过 `POST /api/models/register` 以单层 `package_name` 注册。注册会在写入数据库前校验 schema、制品 SHA 和 CPU 张量契约；它不会创建部署校准 profile。完整命令与安全边界见 [模型包与运行时](docs/model-package.md)。

每个导出 manifest 还声明模型范围：固定项目策略写 `metadata.model_scope="project"`、`metadata.project_key_strategy="fixed"` 和大写 `metadata.supported_project_keys`；按 `Issue_KEY` 前缀动态推导时写 `model_scope="issue_key_prefix"`、`project_key_strategy="issue-key-prefix"`，并将 `supported_project_keys` 留为空数组。空数组表示没有枚举固定的已验证项目，不是通配符，也不是跨项目泛化保证；运行非 demo 模型前仍需在项目配置中提供 `project_key`。

内置 `Deterministic Demo Runtime` 的 seed 元数据可能使用 `supported_project_keys: ["*"]` 作为仅限 synthetic demo 的旧数据兼容标记。它不属于真实 ACCS 模型包契约，导出器不会生成该通配符。

## 测试

运行后端、前端单元测试、类型检查、lint 和生产构建：

```powershell
.\scripts\test.ps1
```

只验证后端：

```powershell
.\scripts\test.ps1 -SkipFrontend
```

若已通过 `bootstrap.ps1 -WithModelRuntime` 安装可选模型运行依赖，且本地已安装 Playwright 浏览器，可附加包含真实 TorchScript 运行路径的端到端测试：

```powershell
.\scripts\test.ps1 -IncludeE2E
```

脚本遇到任何失败都会以非零状态退出。模型 runtime 测试在未安装可选依赖时会跳过；真实模型发布环境应使用 `-WithModelRuntime` 后运行完整测试。当前论文沿用原始 `TRAIN_lab10 + TRAIN_unlab90 -> DEV -> TEST` 协议及学位论文中已报告的结果，不建立新的数据角色或实验流程。表格证据重放和制图环境见 [复现指南](docs/reproducibility.md)。

## 配置与本地隐私

首次初始化会把 `.env.example` 复制为 `.env`。可用变量：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `ACCSTRACE_DATA_DIR` | `./data` | SQLite、导入、导出和日志的本地根目录 |
| `ACCSTRACE_DATABASE_URL` | `sqlite:///./data/accstrace.db` | SQLAlchemy 数据库 URL |
| `ACCSTRACE_FRONTEND_ORIGIN` | `http://127.0.0.1:5173` | 允许的浏览器来源 |
| `ACCSTRACE_MAX_UPLOAD_BYTES` | `20971520` | 单个上传文件字节上限 |
| `ACCSTRACE_WORKER_POLL_SECONDS` | `1.0` | worker 查询队列间隔 |

工具没有用户认证，设计目标是单机使用。不要把 API 绑定到 `0.0.0.0` 或直接暴露到公网。当前应用路径没有遥测或远程模型调用；项目数据、人工决定和导出均留在 `ACCSTRACE_DATA_DIR`。

## 项目结构

```text
backend/app/       FastAPI、SQLAlchemy、候选生成、runtime、worker
alembic/           当前数据库 schema 与后续迁移入口
backend/tests/     后端单元与工作流测试
frontend/          React + TypeScript + Vite 客户端
requirements-lock.txt  Python 3.11 精确第三方依赖锁
requirements-model-runtime-win-x64-lock.txt  Windows x64 CPU 模型 runtime 锁与 wheel hashes
requirements-paper-win-x64-lock.txt  Windows x64 论文表格提取与制图依赖锁
sample_data/       仅用于软件测试的 synthetic CSV
docs/              架构、数据、API、模型包与论文复现说明
scripts/           Windows 初始化、启动、测试与 demo 脚本
data/              本地运行数据（不纳入版本控制）
```

## 论文表述边界

可以将 ACCS-Trace 描述为“面向 Issue-Commit 链接恢复的本地可靠性审查工作流”，但在真实模型接入和独立验证完成前，不能声称：

- 当前仓库已经运行 ACCS checkpoint；
- demo score 是校准概率；
- ACCS 训练期温度就是部署期温度；
- synthetic demo 证明方法优于基线；
- 工具支持任意项目上的可靠自动链接恢复。

软件发布采用 MIT License。
