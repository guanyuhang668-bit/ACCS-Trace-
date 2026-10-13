from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError as PydanticValidationError,
    field_validator,
    model_validator,
)

from .errors import AppError
from .models import ModelRelease


PACKAGE_FORMAT = "accs_torchscript_v1"
PACKAGE_FORMAT_VERSION = "1.0"
PREPROCESS_FORMAT = "legacy_accs_roberta_bpe_v1"
PREPROCESS_FORMAT_VERSION = "1.0"
MANIFEST_FILENAME = "manifest.json"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_TOKENIZER_BYTES = 64 * 1024 * 1024
MAX_PREPROCESSING_BYTES = 16 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class PackageUnavailable(AppError):
    def __init__(self, message: str):
        super().__init__(503, "model_runtime_unavailable", message)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TorchScriptPackageManifest(StrictModel):
    package_format: Literal["accs_torchscript_v1"]
    format_version: Literal["1.0"]
    model_name: str = Field(min_length=1, max_length=160)
    model_version: str = Field(min_length=1, max_length=80)
    source_domain: str = Field(min_length=1, max_length=255)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    producer_torch_version: str = Field(min_length=1, max_length=80)
    model_file: str = Field(min_length=1, max_length=255)
    model_sha256: str = Field(min_length=64, max_length=64)
    tokenizer_file: str = Field(min_length=1, max_length=255)
    tokenizer_sha256: str = Field(min_length=64, max_length=64)
    preprocessing_file: str = Field(min_length=1, max_length=255)
    preprocessing_sha256: str = Field(min_length=64, max_length=64)
    model_signature: Literal[
        "forward(text_input_ids:int64[B,512],code_input_ids:int64[B,512])->raw_logits:float[B,2]"
    ]
    preprocessing: Literal["legacy_accs_roberta_bpe_v1"]
    max_batch_size: int = Field(ge=1, le=512)
    positive_class_index: Literal[1]

    @field_validator("model_name", "model_version", "source_domain")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("package identity fields cannot be whitespace")
        return normalized


class PunktParametersSpec(StrictModel):
    abbreviations: list[str] = Field(default_factory=list, max_length=100_000)
    collocations: list[list[str]] = Field(default_factory=list, max_length=100_000)
    sentence_starters: list[str] = Field(default_factory=list, max_length=100_000)
    orthographic_context: dict[str, int] = Field(default_factory=dict, max_length=1_000_000)

    @field_validator("collocations")
    @classmethod
    def validate_collocations(cls, value: list[list[str]]) -> list[list[str]]:
        if any(len(item) != 2 or not item[0] or not item[1] for item in value):
            raise ValueError("each Punkt collocation must contain two non-empty strings")
        return value


class LegacyACCSPreprocessing(StrictModel):
    preprocess_format: Literal["legacy_accs_roberta_bpe_v1"]
    format_version: Literal["1.0"]
    tokenizer_format: Literal["huggingface-tokenizers-json"]
    max_sequence_length: Literal[512]
    truncation: Literal["legacy-balanced-pair-v1"]
    cls_token_id: int = Field(ge=0, le=2_147_483_647)
    sep_token_id: int = Field(ge=0, le=2_147_483_647)
    pad_token_id: Literal[1]
    unk_token_id: int = Field(ge=0, le=2_147_483_647)
    project_key_strategy: Literal["fixed", "issue-key-prefix"]
    fixed_project_key: str | None = Field(default=None, max_length=255)
    stopwords: list[str] = Field(max_length=100_000)
    sentence_tokenizer: Literal["nltk-punkt-parameters-v1"]
    word_tokenizer: Literal["nltk-treebank-v1"]
    stemmer: Literal["nltk-snowball-english-v1"]
    required_nltk_version: str = Field(min_length=1, max_length=32)
    required_tokenizers_version: str = Field(min_length=1, max_length=32)
    punkt_parameters: PunktParametersSpec

    @model_validator(mode="after")
    def validate_contract(self):
        special_ids = {
            self.cls_token_id,
            self.sep_token_id,
            self.pad_token_id,
            self.unk_token_id,
        }
        if len(special_ids) != 4:
            raise ValueError("legacy ACCS special token ids must be distinct")
        if self.project_key_strategy == "fixed":
            if not self.fixed_project_key or not re.fullmatch(
                r"[A-Za-z0-9_#]+(?:/[A-Za-z0-9_#]+)*", self.fixed_project_key
            ):
                raise ValueError("fixed_project_key is required and must contain safe project keys")
        elif self.fixed_project_key is not None:
            raise ValueError("fixed_project_key must be null for issue-key-prefix strategy")
        if len(self.stopwords) != len(set(self.stopwords)) or any(
            not word or word != word.lower() for word in self.stopwords
        ):
            raise ValueError("stopwords must be unique, non-empty, and lowercase")
        return self


@dataclass(frozen=True, slots=True)
class LoadedModelPackage:
    root: Path
    manifest_path: Path
    model_path: Path
    tokenizer_path: Path
    preprocessing_path: Path
    manifest_sha256: str
    manifest: TorchScriptPackageManifest
    tokenizer_json: dict[str, object]
    preprocessing: LegacyACCSPreprocessing

    @property
    def name(self) -> str:
        return self.manifest.model_name

    @property
    def version(self) -> str:
        return self.manifest.model_version

    @property
    def source_domain(self) -> str:
        return self.manifest.source_domain

    @property
    def metadata(self) -> dict[str, JsonValue]:
        return self.manifest.metadata

    @property
    def runtime_kind(self) -> str:
        return PACKAGE_FORMAT

    @property
    def package_checksum_sha256(self) -> str:
        return self.manifest_sha256


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key '{key}'")
        result[key] = value
    return result


def _read_json(path: Path, byte_limit: int, label: str) -> object:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise PackageUnavailable(f"The ACCS {label} is missing or unreadable") from exc
    if size > byte_limit:
        raise PackageUnavailable(f"The ACCS {label} exceeds the supported size limit")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PackageUnavailable(f"The ACCS {label} must be valid UTF-8") from exc
    except OSError as exc:
        raise PackageUnavailable(f"The ACCS {label} is missing or unreadable") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise PackageUnavailable(f"The ACCS {label} is not valid canonical JSON") from exc


def _require_hash(value: str | None, label: str) -> str:
    if not value or not SHA256_RE.fullmatch(value):
        raise PackageUnavailable(f"The ACCS {label} must be a 64-character SHA-256 digest")
    return value.lower()


def _resolve_package_file(root: Path, reference: str, label: str) -> Path:
    relative = PurePosixPath(reference)
    if (
        "\\" in reference
        or ":" in reference
        or relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise PackageUnavailable(f"The ACCS {label} must be a safe package-relative path")
    try:
        unresolved = root.joinpath(*relative.parts)
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PackageUnavailable(f"The ACCS {label} cannot be a symbolic link")
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(root)
    except PackageUnavailable:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise PackageUnavailable(f"The ACCS {label} is missing or outside the model package") from exc
    if not resolved.is_file():
        raise PackageUnavailable(f"The ACCS {label} is not a regular file")
    return resolved


def validate_model_manifest(
    manifest_path: str | Path,
    expected_manifest_sha256: str | None = None,
) -> LoadedModelPackage:
    configured_manifest = Path(manifest_path)
    if not configured_manifest.is_absolute():
        raise PackageUnavailable("The configured ACCS manifest path must be absolute")
    try:
        manifest_path = configured_manifest.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PackageUnavailable("The configured ACCS package manifest is missing") from exc
    if not manifest_path.is_file() or manifest_path.name != MANIFEST_FILENAME:
        raise PackageUnavailable("The configured ACCS model path must point to manifest.json")
    try:
        root = manifest_path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PackageUnavailable("The configured ACCS package directory is missing") from exc
    try:
        manifest_sha256 = sha256_file(manifest_path)
    except OSError as exc:
        raise PackageUnavailable("The ACCS package manifest is unreadable") from exc
    if expected_manifest_sha256 is not None:
        release_checksum = _require_hash(expected_manifest_sha256, "package checksum")
        if manifest_sha256 != release_checksum:
            raise PackageUnavailable(
                "The ACCS package manifest checksum does not match the release metadata"
            )

    manifest_data = _read_json(manifest_path, MAX_MANIFEST_BYTES, "package manifest")
    try:
        manifest = TorchScriptPackageManifest.model_validate(manifest_data)
    except PydanticValidationError as exc:
        raise PackageUnavailable("The ACCS package manifest does not match format version 1.0") from exc
    model_checksum = _require_hash(manifest.model_sha256, "TorchScript checksum")
    tokenizer_checksum = _require_hash(manifest.tokenizer_sha256, "tokenizer checksum")
    preprocessing_checksum = _require_hash(
        manifest.preprocessing_sha256, "preprocessing checksum"
    )
    model_path = _resolve_package_file(root, manifest.model_file, "TorchScript artifact")
    tokenizer_path = _resolve_package_file(root, manifest.tokenizer_file, "tokenizer artifact")
    preprocessing_path = _resolve_package_file(
        root, manifest.preprocessing_file, "preprocessing artifact"
    )
    if len({manifest_path, model_path, tokenizer_path, preprocessing_path}) != 4:
        raise PackageUnavailable("The ACCS package artifacts must be distinct files")
    try:
        if sha256_file(model_path) != model_checksum:
            raise PackageUnavailable(
                "The ACCS TorchScript checksum does not match the package manifest"
            )
        if sha256_file(tokenizer_path) != tokenizer_checksum:
            raise PackageUnavailable(
                "The ACCS tokenizer checksum does not match the package manifest"
            )
        if sha256_file(preprocessing_path) != preprocessing_checksum:
            raise PackageUnavailable(
                "The ACCS preprocessing checksum does not match the package manifest"
            )
    except OSError as exc:
        raise PackageUnavailable("An ACCS package artifact became unreadable during validation") from exc

    tokenizer_data = _read_json(tokenizer_path, MAX_TOKENIZER_BYTES, "tokenizer artifact")
    if not isinstance(tokenizer_data, dict) or "model" not in tokenizer_data:
        raise PackageUnavailable("The ACCS tokenizer is not a standard tokenizers JSON artifact")
    preprocessing_data = _read_json(
        preprocessing_path, MAX_PREPROCESSING_BYTES, "preprocessing artifact"
    )
    try:
        preprocessing = LegacyACCSPreprocessing.model_validate(preprocessing_data)
    except PydanticValidationError as exc:
        raise PackageUnavailable("The ACCS preprocessing does not match format version 1.0") from exc
    return LoadedModelPackage(
        root=root,
        manifest_path=manifest_path,
        model_path=model_path,
        tokenizer_path=tokenizer_path,
        preprocessing_path=preprocessing_path,
        manifest_sha256=manifest_sha256,
        manifest=manifest,
        tokenizer_json=tokenizer_data,
        preprocessing=preprocessing,
    )


def load_model_package(model: ModelRelease) -> LoadedModelPackage:
    if not model.model_path:
        raise PackageUnavailable("The ACCS model release does not define a manifest path")
    release_checksum = _require_hash(model.checksum_sha256, "package checksum")
    package = validate_model_manifest(model.model_path, release_checksum)
    if (
        package.name != model.name
        or package.version != model.version
        or package.source_domain != model.source_domain
    ):
        raise PackageUnavailable(
            "The ACCS package identity does not match the registered model release"
        )
    return package
