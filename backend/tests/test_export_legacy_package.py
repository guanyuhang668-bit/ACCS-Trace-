from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


torch = pytest.importorskip("torch")
tokenizers = pytest.importorskip("tokenizers")
pytest.importorskip("nltk")

from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from app.model_package import validate_model_manifest
from app.models import CommitArtifact, IssueArtifact, ModelRelease, RuntimeKind
from app.runtime import TorchScriptACCSRuntime, get_runtime


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_legacy_module(path: Path) -> None:
    path.write_text(
        """import torch


class TinyLegacyACCS(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.bias = torch.nn.Parameter(torch.tensor(0.25))

    def get_logits(self, text_input_ids=None, code_input_ids=None):
        evidence = (text_input_ids == 8).sum(dim=1)
        evidence = evidence + (code_input_ids == 8).sum(dim=1)
        evidence = evidence.to(dtype=torch.float32) + self.bias
        return torch.stack((-evidence, evidence), dim=1)
""",
        encoding="utf-8",
    )


def _create_checkpoint(module_dir: Path, checkpoint: Path) -> None:
    _write_legacy_module(module_dir / "legacy_tiny_model.py")
    sys.path.insert(0, str(module_dir))
    try:
        legacy_module = importlib.import_module("legacy_tiny_model")
        with checkpoint.open("wb") as checkpoint_file:
            torch.save(legacy_module.TinyLegacyACCS(), checkpoint_file)
    finally:
        sys.path.remove(str(module_dir))
        sys.modules.pop("legacy_tiny_model", None)


def _create_tokenizer(path: Path) -> None:
    vocab = {
        "<s>": 0,
        "<pad>": 1,
        "</s>": 2,
        "<unk>": 3,
        "encrypt": 4,
        "pdf": 5,
        "password": 6,
        "pars": 7,
        "fix": 8,
        "fail": 9,
        "parser": 10,
    }
    tokenizer = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
    tokenizer.pre_tokenizer = Whitespace()
    _write_json(path, json.loads(tokenizer.to_str()))


@pytest.mark.parametrize(
    ("project_key_strategy", "fixed_project_key", "expected_scope", "expected_keys"),
    [
        ("fixed", "TIKA", "project", ["TIKA"]),
        ("issue-key-prefix", None, "issue_key_prefix", []),
    ],
)
def test_exporter_builds_a_valid_runtime_package_from_a_trusted_legacy_model(
    tmp_path: Path,
    project_key_strategy: str,
    fixed_project_key: str | None,
    expected_scope: str,
    expected_keys: list[str],
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    exporter = repository_root / "scripts" / "export-legacy-accs-package.py"
    legacy_code = tmp_path / "trusted-legacy-code"
    legacy_code.mkdir()
    checkpoint = tmp_path / "trusted-model.pkl"
    _create_checkpoint(legacy_code, checkpoint)
    tokenizer_path = tmp_path / "tokenizer.json"
    _create_tokenizer(tokenizer_path)
    stopwords_path = tmp_path / "stopwords.json"
    _write_json(stopwords_path, ["a", "is", "the", "when"])
    punkt_path = tmp_path / "punkt-parameters.json"
    _write_json(
        punkt_path,
        {
            "abbreviations": [],
            "collocations": [],
            "sentence_starters": [],
            "orthographic_context": {},
        },
    )
    output = tmp_path / "published-model"
    split_digest = "a" * 64

    command = [
        sys.executable,
        str(exporter),
        "--checkpoint",
        str(checkpoint),
        "--tokenizer",
        str(tokenizer_path),
        "--output-dir",
        str(output),
        "--model-name",
        "Tiny Exported ACCS",
        "--model-version",
        "test-v1",
        "--source-domain",
        "synthetic exporter contract test",
        "--legacy-code-dir",
        str(legacy_code),
        "--project-key-strategy",
        project_key_strategy,
        "--stopwords-file",
        str(stopwords_path),
        "--punkt-parameters-json",
        str(punkt_path),
        "--training-temperature",
        "1.75",
        "--training-temperature-fit-split-sha256",
        split_digest,
        "--i-understand-pickle-is-unsafe",
    ]
    if fixed_project_key is not None:
        command[command.index("--stopwords-file"):command.index("--stopwords-file")] = [
            "--fixed-project-key",
            fixed_project_key,
        ]
    result = subprocess.run(
        command,
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "pickle" in result.stderr.lower()
    package = validate_model_manifest((output / "manifest.json").resolve())
    assert package.manifest.model_signature.endswith("->raw_logits:float[B,2]")
    assert package.manifest.preprocessing == "legacy_accs_roberta_bpe_v1"
    assert package.manifest.producer_torch_version == torch.__version__
    assert package.preprocessing.required_tokenizers_version == tokenizers.__version__
    assert package.preprocessing.project_key_strategy == project_key_strategy
    assert package.preprocessing.fixed_project_key == fixed_project_key
    assert (output / "SHA256SUMS").is_file()
    assert f"{_sha256(output / 'manifest.json')}  manifest.json" in (
        output / "SHA256SUMS"
    ).read_text(encoding="ascii")

    manifest_json = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_json["metadata"]["model_scope"] == expected_scope
    assert manifest_json["metadata"]["project_key_strategy"] == project_key_strategy
    assert manifest_json["metadata"]["supported_project_keys"] == expected_keys
    training_temperature = manifest_json["metadata"]["training"]["training_temperature"]
    assert training_temperature == {
        "name": "T_train",
        "value": 1.75,
        "fit_split_sha256": split_digest,
        "deployment_use": "prohibited",
    }
    assert "T_deploy" not in json.dumps(manifest_json)

    release = ModelRelease(
        name=package.name,
        version=package.version,
        runtime_kind=RuntimeKind.ACCS_TORCHSCRIPT_V1.value,
        model_path=str(package.manifest_path),
        checksum_sha256=package.manifest_sha256,
        source_domain=package.source_domain,
        is_demo=False,
        is_available=True,
    )
    runtime = get_runtime(release)
    assert isinstance(runtime, TorchScriptACCSRuntime)
    issue = IssueArtifact(
        dataset_revision_id="revision",
        issue_key="TIKA-101",
        issue_text="Encrypted PDF parsing fails",
        creation_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    commit = CommitArtifact(
        dataset_revision_id="revision",
        commit_sha="abc123",
        commit_text="TIKA-101 fix encrypted PDF parser",
        commit_code="fix(parser)",
        commit_date=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    score = runtime.score(issue, commit)
    assert 0.5 < score <= 1.0


def test_exporter_refuses_to_load_pickle_without_explicit_acknowledgement(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    exporter = repository_root / "scripts" / "export-legacy-accs-package.py"
    result = subprocess.run(
        [
            sys.executable,
            str(exporter),
            "--checkpoint",
            str(tmp_path / "model.pkl"),
            "--tokenizer",
            str(tmp_path / "tokenizer.json"),
            "--output-dir",
            str(tmp_path / "output"),
            "--model-name",
            "No Load",
            "--model-version",
            "test",
            "--source-domain",
            "test",
        ],
        cwd=repository_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "REFUSING TO LOAD PICKLE" in result.stderr
    assert not (tmp_path / "output").exists()
