from __future__ import annotations

import builtins
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

torch = pytest.importorskip("torch")
pytest.importorskip("tokenizers")
pytest.importorskip("nltk")

from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from app.model_package import load_model_package, validate_model_manifest
from app.models import CommitArtifact, IssueArtifact, ModelRelease, RuntimeKind
from app.runtime import RuntimeUnavailable, TorchScriptACCSRuntime, get_runtime
from app.services.imports import parse_datetime
from app.services.runs import process_run

from .conftest import create_project, import_sample


MODEL_SIGNATURE = (
    "forward(text_input_ids:int64[B,512],code_input_ids:int64[B,512])->raw_logits:float[B,2]"
)


class TinyValidModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.bias = torch.nn.Parameter(torch.tensor(0.0))

    def forward(self, text_input_ids, code_input_ids):
        evidence = (text_input_ids == 8).sum(dim=1) + (code_input_ids == 8).sum(dim=1)
        evidence = evidence.to(dtype=torch.float32) + self.bias
        return torch.stack((-evidence, evidence), dim=1)


class WrongShapeModel(torch.nn.Module):
    def forward(self, text_input_ids, code_input_ids):
        return text_input_ids.sum(dim=1).to(dtype=torch.float32)


class NonFiniteModel(torch.nn.Module):
    def forward(self, text_input_ids, code_input_ids):
        values = text_input_ids.sum(dim=1).to(dtype=torch.float32) * 0
        return torch.stack((values, values / values), dim=1)


class IntegerLogitsModel(torch.nn.Module):
    def forward(self, text_input_ids, code_input_ids):
        values = text_input_ids.sum(dim=1)
        return torch.stack((values, values), dim=1)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _trace_model(module: torch.nn.Module, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    example_text = torch.zeros((2, 512), dtype=torch.long)
    example_code = torch.zeros((2, 512), dtype=torch.long)
    traced = torch.jit.trace(module.eval(), (example_text, example_code), check_trace=False)
    with path.open("wb") as model_file:
        torch.jit.save(traced, model_file)


def _build_package(
    tmp_path: Path,
    module: torch.nn.Module | None = None,
    *,
    max_batch_size: int = 2,
) -> tuple[ModelRelease, Path]:
    root = tmp_path / "accs-package"
    model_path = root / "model.ts"
    tokenizer_path = root / "tokenizer" / "tokenizer.json"
    preprocessing_path = root / "preprocessing.json"
    manifest_path = root / "manifest.json"
    _trace_model(module or TinyValidModel(), model_path)
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
        "unrel": 10,
        "build": 11,
        "cleanup": 12,
        "parser": 13,
        "plugin": 14,
        "configur": 15,
    }
    tokenizer = Tokenizer(WordLevel(vocab=vocab, unk_token="<unk>"))
    tokenizer.pre_tokenizer = Whitespace()
    _write_json(tokenizer_path, json.loads(tokenizer.to_str()))
    preprocessing = {
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
        "stopwords": ["a", "is", "the", "when"],
        "sentence_tokenizer": "nltk-punkt-parameters-v1",
        "word_tokenizer": "nltk-treebank-v1",
        "stemmer": "nltk-snowball-english-v1",
        "required_nltk_version": "3.9.2",
        "required_tokenizers_version": "0.20.3",
        "punkt_parameters": {
            "abbreviations": [],
            "collocations": [],
            "sentence_starters": [],
            "orthographic_context": {},
        },
    }
    _write_json(preprocessing_path, preprocessing)
    manifest = {
        "package_format": "accs_torchscript_v1",
        "format_version": "1.0",
        "model_name": "Tiny ACCS TorchScript",
        "model_version": "test-v1",
        "source_domain": "synthetic runtime contract test",
        "metadata": {"architecture": "tiny-contract-model", "test_only": True},
        "producer_torch_version": torch.__version__,
        "model_file": "model.ts",
        "model_sha256": _sha256(model_path),
        "tokenizer_file": "tokenizer/tokenizer.json",
        "tokenizer_sha256": _sha256(tokenizer_path),
        "preprocessing_file": "preprocessing.json",
        "preprocessing_sha256": _sha256(preprocessing_path),
        "model_signature": MODEL_SIGNATURE,
        "preprocessing": "legacy_accs_roberta_bpe_v1",
        "max_batch_size": max_batch_size,
        "positive_class_index": 1,
    }
    _write_json(manifest_path, manifest)
    release = ModelRelease(
        name="Tiny ACCS TorchScript",
        version="test-v1",
        runtime_kind=RuntimeKind.ACCS_TORCHSCRIPT_V1.value,
        model_path=str(manifest_path),
        checksum_sha256=_sha256(manifest_path),
        source_domain="synthetic runtime contract test",
        is_demo=False,
        is_available=True,
        metadata_json={"package_format": "accs_torchscript_v1", "test_only": True},
    )
    return release, root


def _rewrite_manifest(release: ModelRelease, **changes) -> dict:
    manifest_path = Path(release.model_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(changes)
    _write_json(manifest_path, manifest)
    release.checksum_sha256 = _sha256(manifest_path)
    return manifest


def _pairs() -> list[tuple[IssueArtifact, CommitArtifact]]:
    issue = IssueArtifact(
        id="issue",
        dataset_revision_id="revision",
        issue_key="TIKA-101",
        issue_text="Encrypted PDF password parsing fails",
        creation_date=parse_datetime("2025-01-01T00:00:00Z", "date", 1),
    )
    matching = CommitArtifact(
        id="matching",
        dataset_revision_id="revision",
        commit_sha="abc",
        commit_text="TIKA-101 fix encrypted PDF password parsing",
        commit_code="fix(parser)",
        commit_date=parse_datetime("2025-01-02T00:00:00Z", "date", 1),
    )
    unrelated = CommitArtifact(
        id="unrelated",
        dataset_revision_id="revision",
        commit_sha="xyz",
        commit_text="Unrelated build cleanup",
        commit_code="plugins.configure()",
        commit_date=parse_datetime("2025-01-02T00:00:00Z", "date", 1),
    )
    return [(issue, matching), (issue, unrelated)]


def test_torchscript_runtime_scores_on_cpu_in_bounded_consistent_batches(tmp_path: Path) -> None:
    release, _root = _build_package(tmp_path, max_batch_size=2)
    package = validate_model_manifest(release.model_path)
    assert package.name == release.name
    assert package.version == release.version
    assert package.source_domain == release.source_domain
    assert package.manifest_sha256 == release.checksum_sha256
    assert package.package_checksum_sha256 == release.checksum_sha256
    assert package.runtime_kind == "accs_torchscript_v1"
    assert package.metadata["test_only"] is True
    runtime = get_runtime(release)
    assert isinstance(runtime, TorchScriptACCSRuntime)
    pairs = _pairs()
    expanded = [pairs[0], pairs[1], pairs[0], pairs[1], pairs[0]]
    chunk_sizes = []
    original_run_chunk = runtime._run_chunk

    def tracked_run_chunk(chunk):
        chunk_sizes.append(len(chunk))
        return original_run_chunk(chunk)

    runtime._run_chunk = tracked_run_chunk
    scores = runtime.score_batch(expanded)
    assert chunk_sizes == [2, 2, 1]
    assert len(scores) == len(expanded)
    assert all(0 <= score <= 1 for score in scores)
    assert scores[0] > scores[1]
    assert scores[0] == pytest.approx(runtime.score(*pairs[0]))
    assert scores[:2] == pytest.approx(runtime.score_batch(pairs))
    assert runtime.score_batch([]) == []
    assert all(parameter.device.type == "cpu" for parameter in runtime._module.parameters())


def test_legacy_preprocessing_builds_both_issue_routes_and_exact_balanced_layout(
    tmp_path: Path,
) -> None:
    release, _root = _build_package(tmp_path)
    runtime = get_runtime(release)
    issue, matching_commit = _pairs()[0]
    text_ids, code_ids = runtime._preprocessor.encode_pair(issue, matching_commit)
    assert len(text_ids) == len(code_ids) == 512
    assert text_ids[0] == code_ids[0] == 0
    assert text_ids[:13] == [0, 4, 5, 6, 7, 9, 2, 8, 4, 5, 6, 7, 2]
    assert code_ids[:10] == [0, 4, 5, 6, 7, 9, 2, 8, 13, 2]
    assert 4 in text_ids and 4 in code_ids  # stemmed "encrypted" from Issue_Text
    assert 8 in text_ids and 8 in code_ids  # "fix" from message and code
    assert text_ids.count(2) == code_ids.count(2) == 2
    assert text_ids[-1] == code_ids[-1] == 1

    combined = runtime._preprocessor.combine_pair(list(range(600)), list(range(1000, 1600)))
    assert len(combined) == 512
    assert combined[0] == 0
    assert combined[1:255] == list(range(254))
    assert combined[255] == 2
    assert combined[256:511] == list(range(1000, 1255))
    assert combined[511] == 2

    short = runtime._preprocessor.combine_pair([4], [8])
    assert short[:5] == [0, 4, 2, 8, 2]
    assert set(short[5:]) == {1}
    assert runtime._preprocessor.text_process(
        "TIKA-101 Fixes running http://example.test Git-svn-id the parser", "TIKA"
    ) == "fix run parser"


def test_package_rejects_missing_artifact_bad_hash_and_path_escape(tmp_path: Path) -> None:
    release, root = _build_package(tmp_path)
    (root / "model.ts").unlink()
    with pytest.raises(RuntimeUnavailable, match="missing"):
        get_runtime(release)

    release, root = _build_package(tmp_path / "bad-hash")
    with (root / "model.ts").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(RuntimeUnavailable, match="checksum"):
        get_runtime(release)

    release, root = _build_package(tmp_path / "missing-tokenizer")
    (root / "tokenizer" / "tokenizer.json").unlink()
    with pytest.raises(RuntimeUnavailable, match="missing"):
        get_runtime(release)

    release, root = _build_package(tmp_path / "bad-tokenizer-hash")
    with (root / "tokenizer" / "tokenizer.json").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(RuntimeUnavailable, match="checksum"):
        get_runtime(release)

    release, root = _build_package(tmp_path / "missing-preprocessing")
    (root / "preprocessing.json").unlink()
    with pytest.raises(RuntimeUnavailable, match="missing"):
        get_runtime(release)

    release, root = _build_package(tmp_path / "bad-preprocessing-hash")
    with (root / "preprocessing.json").open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(RuntimeUnavailable, match="checksum"):
        get_runtime(release)

    release, root = _build_package(tmp_path / "escape")
    outside = root.parent / "outside.ts"
    _trace_model(TinyValidModel(), outside)
    _rewrite_manifest(release, model_file="../outside.ts", model_sha256=_sha256(outside))
    with pytest.raises(RuntimeUnavailable, match="package-relative|outside"):
        get_runtime(release)


def test_package_requires_manifest_checksum_and_exact_runtime_kind(tmp_path: Path) -> None:
    release, root = _build_package(tmp_path)
    release.checksum_sha256 = "0" * 64
    with pytest.raises(RuntimeUnavailable, match="manifest checksum"):
        get_runtime(release)

    release.checksum_sha256 = _sha256(root / "manifest.json")
    release.runtime_kind = "accs"
    with pytest.raises(RuntimeUnavailable, match="accs_torchscript_v1"):
        get_runtime(release)

    release.runtime_kind = RuntimeKind.ACCS_TORCHSCRIPT_V1.value
    release.model_path = str(root)
    with pytest.raises(RuntimeUnavailable, match="manifest.json"):
        get_runtime(release)


def test_manifest_forbids_python_plugin_and_release_identity_drift(tmp_path: Path) -> None:
    release, _root = _build_package(tmp_path)
    _rewrite_manifest(release, python_plugin="arbitrary_module.py")
    with pytest.raises(RuntimeUnavailable, match="format version 1.0"):
        get_runtime(release)

    release, _root = _build_package(tmp_path / "identity")
    release.version = "different-version"
    with pytest.raises(RuntimeUnavailable, match="identity"):
        get_runtime(release)


@pytest.mark.parametrize("module", [WrongShapeModel(), NonFiniteModel(), IntegerLogitsModel()])
def test_runtime_rejects_invalid_model_outputs(tmp_path: Path, module: torch.nn.Module) -> None:
    release, _root = _build_package(tmp_path, module)
    with pytest.raises(RuntimeUnavailable, match="shape|non-finite|floating point"):
        get_runtime(release)


def test_missing_torch_dependency_has_explicit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release, _root = _build_package(tmp_path)
    package = load_model_package(release)
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("blocked for contract test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(RuntimeUnavailable, match="PyTorch CPU runtime is required"):
        TorchScriptACCSRuntime(package)


@pytest.mark.parametrize("dependency", ["tokenizers", "nltk"])
def test_missing_legacy_dependency_has_explicit_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dependency: str,
) -> None:
    release, _root = _build_package(tmp_path)
    package = load_model_package(release)
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == dependency or name.startswith(dependency + "."):
            raise ImportError("blocked for contract test")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    with pytest.raises(RuntimeUnavailable, match=f"optional '{dependency}' dependency"):
        TorchScriptACCSRuntime(package)


def test_registered_torchscript_runtime_integrates_with_recovery_pipeline(
    client: TestClient,
    app,
    settings,
    sample_issues: bytes,
    sample_commits: bytes,
) -> None:
    _release, root = _build_package(settings.models_dir)
    registration = client.post(
        "/api/models/register",
        json={"package_name": root.name},
    )
    assert registration.status_code == 201, registration.text
    registered_model = registration.json()
    assert registered_model["runtime_kind"] == "accs_torchscript_v1"
    assert registered_model["is_demo"] is False
    assert registered_model["calibration_profiles"] == []
    assert registered_model["package_path"] == f"models/{root.name}"
    assert "model_path" not in registered_model

    with app.state.session_factory() as db:
        release = db.get(ModelRelease, registered_model["id"])
        assert release is not None
        release_id = release.id
        persisted_manifest_path = release.model_path
    assert persisted_manifest_path is not None
    assert Path(persisted_manifest_path).is_file()

    project = create_project(client, "TorchScript Pipeline", project_key="TIKA")
    revision = import_sample(client, project["id"], sample_issues, sample_commits)
    queued = client.post(
        "/api/runs",
        json={
            "project_id": project["id"],
            "dataset_revision_id": revision["id"],
            "model_release_id": release_id,
            "mode": "recover",
            "days_before": 2,
            "days_after": 2,
            "max_candidates_per_issue": 20,
        },
    )
    assert queued.status_code == 201, queued.text
    assert Path(persisted_manifest_path).is_file()
    process_run(app.state.session_factory, queued.json()["id"])
    completed = client.get(f"/api/runs/{queued.json()['id']}").json()
    assert completed["status"] == "completed"
    assert completed["summary_json"]["candidate_count"] > 0
    manifest = client.get(f"/api/runs/{queued.json()['id']}/manifest").json()
    assert manifest["model"]["runtime_kind"] == "accs_torchscript_v1"
    assert manifest["model"]["is_demo"] is False
