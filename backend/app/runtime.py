from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Protocol

from .model_package import LoadedModelPackage, PackageUnavailable, load_model_package
from .models import CommitArtifact, IssueArtifact, ModelRelease, RuntimeKind
from .legacy_preprocessing import LegacyACCSPreprocessor


TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{1,}|[\u4e00-\u9fff]+")


class ModelRuntime(Protocol):
    def score(self, issue: IssueArtifact, commit: CommitArtifact) -> float: ...

    def score_batch(
        self, pairs: Sequence[tuple[IssueArtifact, CommitArtifact]]
    ) -> list[float]: ...


RuntimeUnavailable = PackageUnavailable


def _tokens(value: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_RE.finditer(value or "")}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


@dataclass(slots=True)
class DemoHeuristicRuntime:
    """Deterministic local runtime used only for development and tests."""

    def score(self, issue: IssueArtifact, commit: CommitArtifact) -> float:
        issue_tokens = _tokens(issue.issue_text)
        commit_tokens = _tokens(commit.commit_text)
        code_tokens = _tokens(commit.commit_code)

        text_overlap = _jaccard(issue_tokens, commit_tokens)
        code_overlap = _jaccard(issue_tokens, code_tokens)
        key_match = 1.0 if issue.issue_key.lower() in commit.commit_text.lower() else 0.0

        # Stable tie-breaker keeps demo results deterministic without claiming ML quality.
        digest = hashlib.sha256(f"{issue.issue_key}:{commit.commit_sha}".encode()).digest()
        jitter = int.from_bytes(digest[:2], "big") / 65535 * 0.04

        logit = -2.5 + 6.2 * text_overlap + 2.4 * code_overlap + 4.0 * key_match + jitter
        return max(0.001, min(0.999, 1.0 / (1.0 + math.exp(-logit))))

    def score_batch(
        self, pairs: Sequence[tuple[IssueArtifact, CommitArtifact]]
    ) -> list[float]:
        return [self.score(issue, commit) for issue, commit in pairs]


class TorchScriptACCSRuntime:
    """Offline CPU runtime for the ACCS-Trace TorchScript package format v1.0."""

    def __init__(self, package: LoadedModelPackage):
        self.package = package
        self._torch = self._import_torch()
        self._preprocessor = LegacyACCSPreprocessor(package)
        try:
            with package.model_path.open("rb") as model_file:
                self._module = self._torch.jit.load(
                    model_file,
                    map_location=self._torch.device("cpu"),
                )
            self._module.eval()
            self._validate_model_contract()
        except Exception as exc:
            if isinstance(exc, RuntimeUnavailable):
                raise
            raise RuntimeUnavailable("The ACCS TorchScript artifact could not be loaded on CPU") from exc

    @staticmethod
    def _import_torch():
        try:
            import torch
        except (ImportError, OSError) as exc:
            raise RuntimeUnavailable(
                "PyTorch CPU runtime is required to load an ACCS TorchScript package"
            ) from exc
        return torch

    def _encode_pair(
        self, issue: IssueArtifact, commit: CommitArtifact
    ) -> tuple[list[int], list[int]]:
        return self._preprocessor.encode_pair(issue, commit)

    def _run_chunk(
        self, pairs: Sequence[tuple[IssueArtifact, CommitArtifact]]
    ) -> list[float]:
        encoded = [self._encode_pair(issue, commit) for issue, commit in pairs]
        text_input_ids = self._torch.tensor(
            [item[0] for item in encoded], dtype=self._torch.long, device="cpu"
        )
        code_input_ids = self._torch.tensor(
            [item[1] for item in encoded], dtype=self._torch.long, device="cpu"
        )
        return self._invoke_model(text_input_ids, code_input_ids, len(pairs))

    def _validate_model_contract(self) -> None:
        preprocessing = self.package.preprocessing
        text_input_ids = self._torch.full(
            (1, self.package.preprocessing.max_sequence_length),
            preprocessing.pad_token_id,
            dtype=self._torch.long,
            device="cpu",
        )
        code_input_ids = self._torch.full(
            (1, self.package.preprocessing.max_sequence_length),
            preprocessing.pad_token_id,
            dtype=self._torch.long,
            device="cpu",
        )
        text_input_ids[0, 0] = preprocessing.cls_token_id
        text_input_ids[0, 1] = preprocessing.sep_token_id
        code_input_ids[0, 0] = preprocessing.cls_token_id
        code_input_ids[0, 1] = preprocessing.sep_token_id
        self._invoke_model(text_input_ids, code_input_ids, 1)

    def _invoke_model(self, text_input_ids, code_input_ids, expected_batch: int) -> list[float]:
        try:
            with self._torch.inference_mode():
                output = self._module(text_input_ids, code_input_ids)
        except Exception as exc:
            raise RuntimeUnavailable("ACCS TorchScript inference failed on CPU") from exc
        if not isinstance(output, self._torch.Tensor):
            raise RuntimeUnavailable("The ACCS TorchScript output must be a tensor")
        if output.ndim != 2 or output.shape != (expected_batch, 2):
            raise RuntimeUnavailable(
                "The ACCS TorchScript output must be raw logits with shape [batch, 2]"
            )
        if not self._torch.is_floating_point(output):
            raise RuntimeUnavailable("The ACCS TorchScript raw logits must be floating point")
        output = output.detach().to(device="cpu", dtype=self._torch.float64)
        if not bool(self._torch.isfinite(output).all()):
            raise RuntimeUnavailable("The ACCS TorchScript output contains a non-finite value")
        probabilities = self._torch.softmax(output, dim=1)
        positive_index = self.package.manifest.positive_class_index
        return [float(value) for value in probabilities[:, positive_index].tolist()]

    def score(self, issue: IssueArtifact, commit: CommitArtifact) -> float:
        return self.score_batch([(issue, commit)])[0]

    def score_batch(
        self, pairs: Sequence[tuple[IssueArtifact, CommitArtifact]]
    ) -> list[float]:
        if not pairs:
            return []
        max_batch_size = self.package.manifest.max_batch_size
        scores: list[float] = []
        for start in range(0, len(pairs), max_batch_size):
            scores.extend(self._run_chunk(pairs[start : start + max_batch_size]))
        return scores


def verify_model_release(model: ModelRelease) -> None:
    if model.is_demo:
        if model.runtime_kind != RuntimeKind.DEMO.value:
            raise RuntimeUnavailable("A demo model release must use the demo runtime kind")
        return
    if model.runtime_kind != RuntimeKind.ACCS_TORCHSCRIPT_V1.value:
        raise RuntimeUnavailable(
            "A non-demo model release must use runtime kind 'accs_torchscript_v1'"
        )
    load_model_package(model)


def get_runtime(model: ModelRelease) -> ModelRuntime:
    if model.is_demo:
        verify_model_release(model)
        return DemoHeuristicRuntime()
    if model.runtime_kind != RuntimeKind.ACCS_TORCHSCRIPT_V1.value:
        raise RuntimeUnavailable(
            "A non-demo model release must use runtime kind 'accs_torchscript_v1'"
        )
    return TorchScriptACCSRuntime(load_model_package(model))


def apply_temperature(probability: float, temperature: float) -> float:
    probability = max(1e-7, min(1 - 1e-7, probability))
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    logit = math.log(probability / (1 - probability))
    return 1.0 / (1.0 + math.exp(-(logit / temperature)))
