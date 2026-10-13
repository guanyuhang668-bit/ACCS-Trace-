# 复现与论文发布指南

## 当前论文的唯一实验边界

当前论文只报告原始 ACCS 研究中的协议和已记录在权威学位论文表格中的结果：

```text
TRAIN_lab10 + TRAIN_unlab90 -> DEV -> TEST
```

- `TRAIN_lab10` 提供有标签样本；
- `TRAIN_unlab90` 提供自训练阶段使用的未标注样本；
- `DEV` 用于原方法中的温度拟合、模型选择和停止判断；
- `TEST` 提供最终 F1、F2、Precision、Recall 和 MAP@3。

本仓库不会为当前论文建立额外数据角色，不重新切分数据，不使用网页候选时间窗口调参，也不启动新的训练或测试流程。网页中的时间窗口和每 Issue 候选上限只是控制推理与人工复核工作量的运行参数。

## 可以复现什么

当前归档支持两类不涉及新实验的验证：

1. **软件工作流复现**：用 synthetic demo 验证导入、候选生成、运行、复核、审计和导出。
2. **论文证据重放**：从权威 DOCX 逐单元提取原表，核对 SHA-256，重新生成结果图，并检查稿件数字、图号、引用和证据声明。

当前归档不能重新执行原始 ACCS 训练，也不能从逐候选预测重新计算学位论文的性能表，因为原始任务 checkpoint、训练日志、随机种子记录和逐候选预测尚未归档。这个限制必须在论文中保留；不能用 demo、合成模型或新实验替代原始证据。

## 干净环境初始化

基础软件工作流：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\bootstrap.ps1
.\scripts\test.ps1
```

论文证据提取与制图工具：

```powershell
.\scripts\bootstrap.ps1 -WithPaperTools
```

真实 ACCS TorchScript 包的转换或运行环境仅在拥有合法、可信的原始 checkpoint 时安装：

```powershell
.\scripts\bootstrap.ps1 -WithModelRuntime
```

`-WithPaperTools` 和 `-WithModelRuntime` 可以同时使用，但前者只负责表格提取和制图，后者只负责真实模型包运行；两者都不会自动训练模型。

## 权威表格证据重放

权威源文件为 `2315363092+胡守娅 (1).docx`。当前记录的源文件属性为：

```text
Bytes:   2,432,648
SHA-256: 50f7606cc61b2ee38078864eb0820bd1445ac32b31c93d3181a382077a8d254f
Tables:  14
```

在仓库根目录中指定源文件并使用一个新的输出目录：

```powershell
$sourceDocx = Resolve-Path "..\2315363092+胡守娅 (1).docx"
$replayDir = Join-Path $PWD "paper\qa\evidence_replay"

.\.venv\Scripts\python.exe paper\scripts\extract_original_thesis_evidence.py `
  --source $sourceDocx `
  --output-dir $replayDir
```

然后逐文件比较重放结果与已冻结证据：

```powershell
$evidenceDir = Join-Path $PWD "paper\data\original_evidence"
Get-ChildItem -LiteralPath $evidenceDir -File | ForEach-Object {
  $replayed = Join-Path $replayDir $_.Name
  [pscustomobject]@{
    File = $_.Name
    Frozen = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    Replay = (Get-FileHash -LiteralPath $replayed -Algorithm SHA256).Hash
  }
}
```

六个文件的 `Frozen` 与 `Replay` 必须完全相同。提取脚本只转录表格单元，不重算指标，也不修正源表内部差异。

## 图表和稿件验证

重新生成保留结果图和两套投稿图包：

```powershell
.\.venv\Scripts\python.exe paper\scripts\generate_original_result_figures.py
.\.venv\Scripts\python.exe paper\scripts\generate_figures.py
.\.venv\Scripts\python.exe paper\scripts\prepare_submission_figures.py
.\.venv\Scripts\python.exe paper\scripts\prepare_software_impacts_figures.py
```

运行静态稿件验证：

```powershell
.\.venv\Scripts\python.exe paper\scripts\validate_original_manuscript.py
.\.venv\Scripts\python.exe paper\scripts\validate_software_impacts_osp.py
```

这些命令验证图表来源、manifest、引用、编号、结果表和论文边界。它们不会训练 ACCS、生成新数据划分、选择候选规则或改变原始结果。

## 依赖锁

- `requirements-lock.txt`：Web API、测试和基础运行环境；
- `requirements-model-runtime-win-x64-lock.txt`：可选 CPU TorchScript 模型运行环境；
- `requirements-paper-win-x64-lock.txt`：可选 DOCX 表格提取与 Matplotlib/Pillow 制图环境。

三个 lock 均面向 CPython 3.11；后两个发布 lock 当前面向 Windows x86-64。发布其他平台前应从对应 `.in` 文件生成并验证该平台自己的 lock，不能把 Windows lock 描述成跨平台依赖锁。

## 软件工作流验收

软件测试应至少覆盖：

| 检查 | 可观察结果 |
| --- | --- |
| 健康检查 | `/api/health` 返回 200，database 为 `ok` |
| 输入校验 | 缺列、非 UTF-8、重复 key 和错误日期返回结构化错误 |
| 数据版本 | 输入 SHA-256 和 Issue/Commit 行数可追踪 |
| 候选生成 | 时间窗口和候选上限写入 run config |
| 模型边界 | demo 明示非 ACCS；真实包 schema、hash 或 CPU 契约错误时明确失败 |
| 校准边界 | 未验证 profile 时不伪造 calibrated probability |
| 人工复核 | 决定持久化并带并发版本检查 |
| 导出 | 行数、模型版本、run ID 和 SHA-256 可核对 |
| 审计 | run、复核、导出、取消和 retry 事件可查询 |
| 离线 | 安装依赖后，demo 工作流不调用远程模型服务 |

Synthetic demo 只能证明软件行为，不能证明 ACCS 的准确率、概率校准质量或跨项目泛化能力。

## 真实模型接入边界

网页要运行真正的 ACCS，而不是演示 runtime，仍需要作者提供可合法发布的原始 checkpoint。转换必须在受信任环境进行，并冻结：

- TorchScript raw-logit 模型；
- tokenizer 和预处理配置；
- 原始 checkpoint 与导出文件 SHA-256；
- 支持项目或模型适用域；
- 决策阈值及其来源；
- 模型版本、许可证和已知限制。

缺少真实模型包不会改变学位论文中保留结果的数值，但会限制工具论文能够声称的功能：当前公开包只能展示完整工作流，不能声称已经用真实 ACCS checkpoint 完成网页推理。

## 投稿前作者材料

以下项目属于发布与署名工作，不是新增实验：

- [ ] 作者、单位、通讯作者和 CRediT 信息已确认；
- [ ] funding、conflict of interest 和 data/code availability 已确认；
- [ ] 源代码有公开 release、固定 tag、许可证和永久存档 DOI；
- [ ] 所有稿件占位符已替换；
- [ ] 若许可允许，真实 ACCS 模型包和模型卡已归档；若不能发布，正文给出准确理由；
- [ ] 正式稿套入目标期刊模板并完成页数、图分辨率和参考文献格式检查；
- [ ] README、界面和论文始终把 synthetic demo 与 ACCS 性能证据分开；
- [ ] 源表中已披露的内部差异保持原样，不被静默“修正”。
