# ACCS TorchScript 模型包

本文描述后端已经实现的 `accs_torchscript_v1` 契约、旧 ACCS checkpoint 的受信任转换流程，以及训练期和部署期校准的边界。

## 当前状态

仓库已经实现以下能力：

- 严格解析并校验 `accs_torchscript_v1` manifest；
- 离线加载 CPU TorchScript，并验证输入、输出 shape、dtype 和有限值；
- 按 `legacy_accs_roberta_bpe_v1` 复现两路预处理；
- 从 `ACCSTRACE_DATA_DIR/models/` 的直接子目录受控注册模型；
- 用模型 raw logits 计算二分类正类 raw probability。

仓库**没有附带真实 ACCS 任务 checkpoint**。基础 RoBERTa/CodeBERT 权重不是任务模型，内置 demo runtime 也不是 ACCS。只有经过 ACCS 训练、转换、验证并注册的包才能作为真实模型运行。

## 包目录

转换器生成的目录如下：

```text
accs-release-name/
  manifest.json
  preprocessing.json
  SHA256SUMS
  model/
    model.ts
  tokenizer/
    tokenizer.json
```

`SHA256SUMS` 用于人工归档核对。后端以 `manifest.json` 的 SHA-256 标识已注册 release，并根据 manifest 内三个 SHA-256 分别验证 TorchScript、tokenizer 和 preprocessing。

## 张量契约

模型必须实现唯一签名：

```text
forward(text_input_ids:int64[B,512],code_input_ids:int64[B,512])->raw_logits:float[B,2]
```

| 张量 | shape | 语义 |
| --- | --- | --- |
| `text_input_ids` | `[B, 512]` | `Issue_Text` 与 `Commit_Text` 的配对序列 |
| `code_input_ids` | `[B, 512]` | `Issue_Text` 与 `Commit_Code` 的配对序列 |
| `raw_logits` | `[B, 2]` | 未 softmax、未温度缩放的二分类 logits |

标签索引固定为 `0=non-link`、`1=link`，所以 `positive_class_index` 必须是 `1`。运行时执行 `softmax(raw_logits)[:, 1]` 得到 `raw_probability`。模型不得把 softmax、阈值或部署温度固化进 TorchScript。

旧 `BTModel.forward()` 返回 softmax probability，而 `BTModel.get_logits()` 才返回 raw logits。转换器因此包装 `get_logits()`，不能直接导出旧 `forward()`。

## Manifest 契约

[model-manifest.example.json](model-manifest.example.json) 给出字段完整、但使用零摘要占位且不可注册的示例；正式 manifest 应由转换器生成。schema 使用 strict/extra-forbid 校验，核心字段为：

- `package_format="accs_torchscript_v1"`、`format_version="1.0"`；
- `model_name`、`model_version`、`source_domain` 和公开 `metadata`；
- `metadata.model_scope`、`metadata.project_key_strategy` 和 `metadata.supported_project_keys`；
- `producer_torch_version`；
- 三个包内相对路径及对应 SHA-256；
- 上述精确 `model_signature`；
- `preprocessing="legacy_accs_roberta_bpe_v1"`；
- `max_batch_size`，范围 `1..512`；
- `positive_class_index=1`。

路径必须使用安全的 POSIX 相对路径。校验器拒绝绝对路径、反斜杠、盘符、`.`、`..`、符号链接、缺失文件、复用同一制品文件和 checksum 不一致。运行时不会联网补齐任何文件。

### 模型适用范围

转换器在 manifest 的 `metadata` 中写入结构化范围声明：

```json
{
  "model_scope": "project",
  "project_key_strategy": "fixed",
  "supported_project_keys": ["TIKA"]
}
```

固定策略表示清洗规则绑定到一个明确的、以大写表示的训练项目键；数组中的键是该 release 声称支持的有限集合，不代表任意项目都已验证。若使用从 `Issue_KEY` 的第一个 `-` 前动态提取项目键的策略，manifest 应写成：

```json
{
  "model_scope": "issue_key_prefix",
  "project_key_strategy": "issue-key-prefix",
  "supported_project_keys": []
}
```

空数组是有意的：它表示没有可诚实列举的固定项目集合，不表示模型不能运行，也不等同于通配符。该策略仍要求每个项目配置合法的 `project_key`，且输入 `Issue_KEY` 含有可由第一个 `-` 前推导的项目键；它只声明预处理如何推导键，不声明跨项目泛化已经得到验证。`source_domain` 和论文评估证据仍决定模型的实际适用边界。

仓库内置的 `Deterministic Demo Runtime` 是旧数据兼容例外：seed 元数据可能暂时包含 `supported_project_keys: ["*"]`，以便没有 `project_key` 的 synthetic demo 继续运行。该值不是 `accs_torchscript_v1` 真实发布包的范围声明，不能从 demo 复制到 ACCS 导出包；真实包必须使用上面的 fixed 列表或 prefix 空数组约定。

## 预处理契约

`preprocessing.json` 同样采用严格的 `legacy_accs_roberta_bpe_v1/1.0` schema：

```json
{
  "preprocess_format": "legacy_accs_roberta_bpe_v1",
  "format_version": "1.0",
  "tokenizer_format": "huggingface-tokenizers-json",
  "max_sequence_length": 512,
  "truncation": "legacy-balanced-pair-v1",
  "cls_token_id": 0,
  "sep_token_id": 2,
  "pad_token_id": 1,
  "unk_token_id": 3,
  "project_key_strategy": "fixed",
  "fixed_project_key": "TIKA",
  "stopwords": ["a", "the"],
  "sentence_tokenizer": "nltk-punkt-parameters-v1",
  "word_tokenizer": "nltk-treebank-v1",
  "stemmer": "nltk-snowball-english-v1",
  "required_nltk_version": "3.9.2",
  "required_tokenizers_version": "0.20.3",
  "punkt_parameters": {
    "abbreviations": [],
    "collocations": [],
    "sentence_starters": [],
    "orthographic_context": {}
  }
}
```

运行时先执行旧 ACCS 的 URL/Issue tag/Git 标记清理、英文数字过滤、Punkt 分句、Treebank 分词、停用词删除和 Snowball stemming，再分别组成：

```text
[CLS] issue [SEP] commit_message [SEP]
[CLS] issue [SEP] commit_code    [SEP]
```

两路序列使用旧的 balanced-pair 截断并右侧 padding 到 512。`project_key_strategy` 可固定为训练时的 `args.key`，也可从每个 `Issue_KEY` 的第一个 `-` 前提取；发布者必须选择与训练完全一致的策略，并同步填写上面的范围 metadata。安装的 NLTK/tokenizers 版本必须和 preprocessing 文件精确相同。

## 转换旧 checkpoint

> **安全边界：**旧训练代码使用 `torch.save(model)` 保存完整 Python 对象。加载这种 pickle 可以执行任意代码。只可在隔离的、受信任的训练/转换环境中加载自己验证过的 checkpoint 和旧源码。ACCS-Trace 部署运行时只加载转换后的 TorchScript，绝不加载旧 pickle。

转换脚本本身不要求安装 FastAPI、Pydantic 或 SQLAlchemy。应在隔离的旧训练环境中准备 PyTorch、tokenizers、NLTK，以及反序列化 checkpoint 所需的原始 Transformers/模型类；依赖和 NLTK 数据提前准备好，转换过程不需要联网。若该环境也安装了 ACCS-Trace 后端依赖，脚本会追加运行仓库 schema validator；否则明确跳过这一步，并由后续注册和仓库测试强制完成验证。示例：

```powershell
python .\scripts\export-legacy-accs-package.py `
  --checkpoint <trusted-checkpoint-file> `
  --legacy-code-dir <trusted-legacy-source-directory> `
  --tokenizer <local-tokenizer-json-or-roberta-directory> `
  --output-dir .\data\models\accs-paper-v1 `
  --model-name "ACCS Issue-Commit" `
  --model-version "paper-v1" `
  --source-domain "declared training projects" `
  --project-key-strategy fixed `
  --fixed-project-key TIKA `
  --max-batch-size 16 `
  --training-code-commit <training-code-commit> `
  --training-temperature <T_train> `
  --training-temperature-fit-split-sha256 <64-hex-digest> `
  --i-understand-pickle-is-unsafe
```

Tokenizer 输入可以是现成 `tokenizer.json`，或包含 `vocab.json`/`merges.txt` 的本地 RoBERTa 目录。后一种情况会用本地 `RobertaTokenizer` 做 token-ID parity 检查。转换器还会：

1. 固化 NLTK stopwords 和 Punkt 参数，不在部署时读取或下载 corpus；
2. 在 CPU 上比较旧 `get_logits()` 与 TorchScript 在 batch 1、2、3 上的输出；
3. 生成规范 JSON 和三个制品 SHA-256；
4. 在后端依赖可用时调用 `validate_model_manifest()`；无 Web 栈的训练环境会明确跳过，注册 API 仍会强制校验；
5. 输出 manifest SHA-256 和 `SHA256SUMS`。

若转换环境没有原训练所用 NLTK corpus，可显式提供只读快照：

```text
--stopwords-file <json-array-or-line-file>
--punkt-parameters-json <frozen-punkt-parameters.json>
```

不得为了让转换“通过”而换用不同的 tokenizer、stopwords、Punkt 参数或项目键策略。正式论文包还应补充训练环境与部署环境的 golden inference cases；当前转换器的合成对照只能验证导出机制。

## 安装和受控注册

默认 bootstrap 只安装轻量 demo 环境。仓库实际发布的模型锁是 Windows x86-64 CPU 专用的 `requirements-model-runtime-win-x64-lock.txt`；真实包需要显式安装该带 hashes 的 lock：

```powershell
.\scripts\bootstrap.ps1 -WithModelRuntime
```

将包放在 `ACCSTRACE_DATA_DIR/models/<package_name>/`；`package_name` 只能是单层安全目录名。API 不接受客户端文件路径，也不负责上传或解压模型。服务启动后注册：

```powershell
$body = @{ package_name = "accs-paper-v1" } | ConvertTo-Json
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/models/register `
  -ContentType "application/json" `
  -Body $body
```

注册过程先校验 manifest 和所有制品 SHA，再在 CPU 上加载模型并执行张量契约探针；全部成功后才持久化可用 release。重复的 `model_name + model_version` 返回冲突。注册不创建、拟合或验证 calibration profile。

## `T_train` 与 `T_deploy`

| 参数 | 所属阶段 | 允许用途 |
| --- | --- | --- |
| `T_train` | ACCS 训练/自训练内部 | 伪标签置信度或样本选择；只能作为训练溯源 metadata |
| `T_deploy` | 冻结模型后的独立部署校准 | 将展示概率与经验正确率对齐；属于独立 calibration profile |

正确发布顺序是：

1. 用 ACCS 完成训练并冻结模型；
2. 转换器导出只产生 raw logits 的模型包；
3. 在与训练隔离的 calibration split 上拟合 `T_deploy`；
4. 在另一个独立 test split 报告 NLL、Brier、ECE、precision/coverage；
5. 把 profile 绑定到精确 model release 和数据 hash。

`T_train` 绝不能复制进 `CalibrationProfile.temperature` 后标为已验证。当前仓库尚未实现 calibration profile 的注册/拟合 API；没有独立验证 profile 时，`calibrated_probability` 保持 `null`。

## 当前限制

- 仓库没有可供论文实验使用的真实 checkpoint 或 calibration profile；
- 首版只支持 CPU TorchScript 和固定 512-token 双输入协议；
- 旧 pickle 的转换依赖发布者提供可信源码和兼容训练依赖；
- TorchScript producer/runtime 版本兼容性必须通过目标发布环境验证；
- 模型包校验保证完整性，不证明制品作者身份或跨项目泛化能力。
