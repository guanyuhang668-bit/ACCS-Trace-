# 复现指南

本指南说明如何在干净环境中复现 ACCS-Trace 的软件行为，以及接入真实评分模型时需要冻结的内容。

## 可以验证什么

1. **软件工作流**：用 `sample_data/` 的合成数据走通导入、候选生成、运行、复核、审计与导出全流程。
2. **真实模型包推理**：在拥有合法 checkpoint 的前提下，用官方导出器生成 `accs_torchscript_v1` 包，注册后由 worker 在本机 CPU 上完成推理。

本仓库不包含 ACCS 任务 checkpoint，也不训练模型。内置 `Deterministic Demo Runtime` 是确定性启发式评分器，只服务于开发、端到端测试与界面演示；它的分数不是校准概率，不能用于方法性能结论。

## 干净环境初始化

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
.\scripts\test.ps1
```

需要运行真实 TorchScript 模型包时追加可选运行环境（当前发布 Windows x86-64 的带哈希锁）：

```powershell
.\scripts\bootstrap.ps1 -WithModelRuntime
```

只安装 Python 侧、跳过前端依赖时加 `-SkipFrontend`。

## 依赖锁

| 文件 | 用途 |
| --- | --- |
| `requirements-lock.txt` | Web API、worker、测试与基础运行环境 |
| `requirements-model-runtime-win-x64-lock.txt` | 可选 CPU TorchScript 推理环境（torch 2.5.1+cpu、tokenizers 等） |
| `frontend/package-lock.json` | 前端依赖 |

两个 Python 锁面向 CPython 3.11。模型运行锁只发布 Windows x86-64 版本；其他平台应从 `requirements-model-runtime.in` 生成并验证对应平台的锁，不能把 Windows 锁当作跨平台依赖锁。

## 软件验收清单

| 检查 | 可观察结果 |
| --- | --- |
| 健康检查 | `/api/health` 返回 200，`database` 为 `ok` |
| 输入校验 | 缺列、非 UTF-8、重复 key 与非法日期返回结构化错误 |
| 数据版本 | 输入 SHA-256 与 Issue/Commit 行数可追踪 |
| 候选生成 | 时间窗口与每 Issue 候选上限写入 run config |
| 模型边界 | demo 明示非 ACCS；真实包 schema、制品哈希或 CPU 张量契约不符时明确失败 |
| 校准边界 | 未经验证的部署 profile 不产生 `calibrated_probability`（返回 `null`） |
| 人工复核 | 决定持久化，并带乐观锁版本检查（过期写入返回 409） |
| 导出 | 行数、模型版本、run ID 与文件 SHA-256 可核对 |
| 审计 | run、复核、导出、取消与 retry 事件可查询 |
| 离线 | 安装依赖后，demo 工作流不调用任何远程服务 |

`.\scripts\test.ps1` 会依次跑后端、前端单元测试、类型检查、lint 与生产构建；加 `-IncludeE2E` 追加 Playwright 端到端用例（需已安装模型运行环境与浏览器）。

## 真实模型包接入

转换必须在受信任的隔离环境中进行，导出器只接受旧训练代码保存的 `torch.save` checkpoint，输出双输入 raw-logits TorchScript、离线分词与预处理配置以及 SHA-256 清单：

```powershell
.\.venv\Scripts\python.exe .\scripts\export-legacy-accs-package.py --help
```

生成的包冻结以下内容：

- TorchScript 模型（`forward(text_input_ids[int64,B,512], code_input_ids[int64,B,512]) -> raw_logits[float,B,2]`）；
- tokenizer 与预处理配置（`legacy_accs_roberta_bpe_v1`）；
- 每个制品的 SHA-256；
- 模型适用范围（`model_scope`、`project_key_strategy`、`supported_project_keys`）；
- 模型版本、许可证与已知限制。

把包目录放到 `ACCSTRACE_DATA_DIR/models/<package_name>/`，再通过 `POST /api/models/register` 注册。注册会在写库前校验 schema、制品摘要与 CPU 张量契约。完整契约与安全边界见[模型包与运行时](model-package.md)。

## 已知边界

- 单机单用户：无认证、无 TLS，仅监听本机回环地址，不应暴露到公网；
- 训练期温度不用于部署：只有独立验证过的部署 profile 才会产生校准概率；
- 取消延迟、超大字段的数据包络、许可与再分发限制见[系统架构](architecture.md)、[数据格式](data-format.md)与[模型包与运行时](model-package.md)。
