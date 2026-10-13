# 系统架构与功能边界

## 工具主题

ACCS-Trace 的核心任务不是训练分类器，而是把冻结的 Issue-Commit 评分模型变成一个可审查、可追踪、可导出的维护工具：

```text
项目身份 -> 数据版本 -> 匹配的冻结模型 -> 候选约束 -> 推理与分级 -> 人工复核 -> 审计导出
```

这种定位把研究方法和工具价值分开。ACCS 负责在少量标注与大量未标注数据上训练任务模型；ACCS-Trace 负责部署该模型、控制建议风险并保存使用证据。工具只保留 ACCS 主线，不组合其他算法。

## 运行组件

| 组件 | 当前职责 | 技术 |
| --- | --- | --- |
| Web client | 项目、导入、运行配置、候选浏览、复核、分析与导出界面 | React、TypeScript、Vite |
| API | 输入校验、事务操作、查询、任务排队、导出下载 | FastAPI、Pydantic、SQLAlchemy |
| Worker | 领取排队任务、候选生成、runtime 推理、分级、进度和失败记录 | 独立 Python 进程 |
| Metadata store | 项目、数据版本、模型元数据、run、候选、复核版本、审计 | SQLite WAL、Alembic migration |
| File store | 原始 CSV 与导出制品 | `ACCSTRACE_DATA_DIR` 下的本地文件 |

SQLite 不是可选缓存。它为人工复核的乐观锁、追加式决定历史、任务状态与审计事件提供事务边界。CSV 和模型权重等较大制品保存在文件系统，数据库只保留路径、摘要与结构化元数据。

## Recover 数据流

1. 用户创建项目并提供项目键，例如 `TIKA`；省略时，首次导入会在所有 `Issue_KEY` 都具有一致前缀时自动推断。
2. API 校验文件类型、UTF-8、表头、必填值、ISO-8601 日期、重复标识，并核对 `Issue_KEY` 前缀与项目键。
3. 每次成功导入形成不可混用的 dataset revision，并保存两个输入文件的 SHA-256。
4. 模型包声明固定的 `supported_project_keys`，或显式声明按 Issue key 前缀预处理。未声明范围的真实模型不会出现在可选列表中。
5. 用户选择 dataset revision、与项目键兼容的 model release、可选 calibration profile 和候选窗口，创建 queued run。API 再次执行同一兼容性校验；第一次运行后项目键锁定。
6. Worker 在处理和重试旧任务时重新校验项目与模型范围，再生成每个 Issue 的时间窗口候选。
7. Runtime 对候选分批输出 `raw_score`。
8. 只有 calibration profile 明确标记 `is_deployment_validated=true` 时，系统才计算 `calibrated_probability`；否则该字段为 `null`。
9. 系统根据决策分数和两个阈值划分 `recommended`、`review`、`rejected`。
10. 用户可接受、拒绝或跳过候选。每次决定都追加保存，并通过 `expected_version` 阻止旧页面覆盖新决定。
11. 完成的 run 可导出 CSV 或 JSON，文件记录 SHA-256、模型名、模型版本和 run ID；run manifest 同时冻结项目键和模型范围声明。

项目键是数据域与模型域之间的安全边界，不是展示标签。当前 ACCS checkpoint 按项目训练，因此常规发布应使用固定项目范围，例如 Tika 模型只声明 `TIKA`。动态 Issue-key 策略只描述预处理方式，不构成跨项目泛化证据，仍须结合模型来源域和独立实验判断是否适用。

## 候选窗口语义

对每个 Issue：

```text
window_start = creation_date - days_before
base_end     = resolution_date
               或 creation_date + unresolved_window_days
window_end   = base_end + days_after
```

只保留提交时间位于闭区间 `[window_start, window_end]` 的 Commit，再按 Commit 与 `base_end` 的时间距离排序，每个 Issue 最多保留 `max_candidates_per_issue` 个候选。这个步骤是工程候选约束，不是 ACCS 模型的一部分。

## 分数与可靠性

设 runtime 输出原始概率样分数 `p_raw`。若且仅若部署校准已在独立数据上验证，温度缩放为：

```text
logit(p_raw) = ln(p_raw / (1 - p_raw))
p_deploy     = sigmoid(logit(p_raw) / T_deploy)
```

决策分数 `p_decision` 为有效的 `p_deploy`，否则为 `p_raw`：

```text
p_decision >= threshold_high  -> recommended
p_decision >= threshold_low   -> review
otherwise                     -> rejected
```

未验证校准时仍允许 demo 工作流运行，但 API 明确返回 `calibrated_probability=null` 和 `calibration_validated=false`。因此“推荐”只是按当前阈值形成的工作队列类别，不能自动解释为具有统计覆盖保证的可靠概率。

## 故障与恢复边界

- API 与 worker 使用同一个 SQLite 数据库；worker 重启时会把未完成任务标记为 interrupted。
- `failed` 或 `interrupted` run 可通过 retry 端点清除旧候选、错误和进度后重新排队，并保留审计事件。
- queued run 可取消；正在评分的 run 具有取消标志。大规模运行前仍应验证取消延迟。
- 只有 completed run 可以导出。
- 真实模型包在注册和每次加载时检查安全路径、manifest 与制品 SHA-256；只有通过 CPU 张量契约探针的 `accs_torchscript_v1` 包才能进入推理。
- 导出下载会验证文件仍位于配置的 export 根目录。

## 安全与部署假设

当前版本面向单用户本机：

- 开发脚本仅监听 `127.0.0.1`；
- CORS 只允许配置的本地 frontend origin；
- 文件名取 basename，存储路径由服务端生成；
- 没有认证、TLS、租户隔离或公网部署支持；
- 没有遥测或远程推理调用；
- 浏览器必须把 Issue 与 Commit 内容当文本渲染，不能作为 HTML 注入。

如果未来需要团队服务器，必须先增加认证、授权、上传恶意内容检查、反向代理/TLS、密钥管理和多用户并发测试。

## 尚未实现或未随仓库发布

- 可用于论文实验的真实 ACCS checkpoint；TorchScript 推理适配器和本地受控注册 API 已实现；
- model release 注销 API，以及 calibration profile 的注册/拟合 API；受控本地模型包注册 API 与界面已经实现；
- Adapt、重新训练、主动学习和部署温度拟合；
- Evaluate 工作流和论文统计报告生成；
- 多用户协作、权限与远程部署。

这些内容只能写入 future work，不能作为当前软件贡献声明。
