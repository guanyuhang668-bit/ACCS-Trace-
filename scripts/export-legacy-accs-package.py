#!/usr/bin/env python3
"""Export a trusted legacy ACCS pickle as an offline TorchScript model package.

This script deliberately loads a full-object ``torch.save(model)`` checkpoint.
Python pickle can execute arbitrary code while loading, so the checkpoint and
every module imported by it must come from a trusted training environment.
The deployed ACCS-Trace runtime never loads the legacy pickle.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


PACKAGE_FORMAT = "accs_torchscript_v1"
PACKAGE_FORMAT_VERSION = "1.0"
PREPROCESS_FORMAT = "legacy_accs_roberta_bpe_v1"
PREPROCESS_FORMAT_VERSION = "1.0"
MODEL_SIGNATURE = (
    "forward(text_input_ids:int64[B,512],code_input_ids:int64[B,512])"
    "->raw_logits:float[B,2]"
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a TRUSTED legacy torch.save(model) ACCS checkpoint into the "
            "offline accs_torchscript_v1 package consumed by ACCS-Trace."
        )
    )
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument(
        "--tokenizer",
        required=True,
        type=Path,
        help="tokenizer.json, a directory containing it, or a RoBERTa vocab.json/merges.txt directory",
    )
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--source-domain", required=True)
    parser.add_argument("--max-batch-size", type=int, default=16)
    parser.add_argument(
        "--project-key-strategy",
        choices=("fixed", "issue-key-prefix"),
        default="fixed",
    )
    parser.add_argument(
        "--fixed-project-key",
        help="Legacy args.key value, for example TIKA or HADOOP/MAPREDUCE",
    )
    parser.add_argument(
        "--legacy-code-dir",
        action="append",
        default=[],
        type=Path,
        help="Trusted directory needed to import classes referenced by the pickle; repeat as needed",
    )
    parser.add_argument(
        "--stopwords-file",
        type=Path,
        help="Optional offline JSON string list or one-word-per-line file; otherwise use local NLTK data",
    )
    parser.add_argument(
        "--punkt-parameters-json",
        type=Path,
        help="Optional frozen Punkt parameter JSON; otherwise use local NLTK english.pickle",
    )
    parser.add_argument("--training-code-commit")
    parser.add_argument("--training-temperature", type=float)
    parser.add_argument("--training-temperature-fit-split-sha256")
    parser.add_argument(
        "--i-understand-pickle-is-unsafe",
        action="store_true",
        help="Required acknowledgement that all pickle inputs and imported legacy code are trusted",
    )
    return parser


def _fail(message: str) -> "None":
    raise SystemExit(f"error: {message}")


def _canonical_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_file(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        _fail(f"{label} is missing: {path} ({exc})")
    if not resolved.is_file():
        _fail(f"{label} must be a regular file: {resolved}")
    return resolved


def _resolve_directory(path: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except OSError as exc:
        _fail(f"{label} is missing: {path} ({exc})")
    if not resolved.is_dir():
        _fail(f"{label} must be a directory: {resolved}")
    return resolved


def _prepare_tokenizer(source: Path, destination: Path):
    try:
        from tokenizers import ByteLevelBPETokenizer, Tokenizer
    except (ImportError, OSError) as exc:
        _fail(f"tokenizers is required in the conversion environment ({exc})")

    source = source.expanduser().resolve(strict=True)
    if source.is_file():
        tokenizer_json = source
        legacy_directory = None
    elif source.is_dir() and (source / "tokenizer.json").is_file():
        tokenizer_json = source / "tokenizer.json"
        legacy_directory = None
    elif source.is_dir() and (source / "vocab.json").is_file() and (source / "merges.txt").is_file():
        tokenizer_json = None
        legacy_directory = source
    else:
        _fail(
            "tokenizer must be tokenizer.json or a directory containing tokenizer.json "
            "or vocab.json plus merges.txt"
        )

    try:
        if tokenizer_json is not None:
            tokenizer = Tokenizer.from_file(str(tokenizer_json))
        else:
            legacy = ByteLevelBPETokenizer(
                str(legacy_directory / "vocab.json"),
                str(legacy_directory / "merges.txt"),
                add_prefix_space=False,
                lowercase=False,
                trim_offsets=False,
            )
            tokenizer = Tokenizer.from_str(legacy.to_str())
            _verify_slow_roberta_parity(legacy_directory, tokenizer)
    except SystemExit:
        raise
    except Exception as exc:
        _fail(f"the tokenizer could not be converted ({exc})")

    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(tokenizer.to_str())
    _canonical_json(destination, payload)
    reloaded = Tokenizer.from_file(str(destination))
    special_ids = {
        "cls_token_id": reloaded.token_to_id("<s>"),
        "sep_token_id": reloaded.token_to_id("</s>"),
        "pad_token_id": reloaded.token_to_id("<pad>"),
        "unk_token_id": reloaded.token_to_id("<unk>"),
    }
    if any(value is None for value in special_ids.values()):
        _fail("tokenizer is missing one of the required RoBERTa tokens: <s>, </s>, <pad>, <unk>")
    if special_ids["pad_token_id"] != 1:
        _fail("legacy_accs_roberta_bpe_v1 requires <pad> token id 1")
    if len(set(special_ids.values())) != 4:
        _fail("the four required special tokens must have distinct ids")
    return reloaded, special_ids


def _verify_slow_roberta_parity(source: Path, tokenizer) -> None:
    try:
        from transformers import RobertaTokenizer
    except (ImportError, OSError) as exc:
        _fail(
            "converting vocab.json/merges.txt requires the trusted training environment's "
            f"transformers package for parity verification ({exc})"
        )
    try:
        slow = RobertaTokenizer.from_pretrained(str(source), local_files_only=True)
    except Exception as exc:
        _fail(f"the legacy RoBERTa tokenizer could not be loaded locally ({exc})")
    samples = (
        "fix parser failure",
        " leading space and punctuation 123",
        "camelCase snake_case URL cleanup",
    )
    for sample in samples:
        expected = slow.convert_tokens_to_ids(slow.tokenize(sample))
        actual = tokenizer.encode(sample, add_special_tokens=False).ids
        if expected != actual:
            _fail("converted tokenizer.json does not match the legacy RobertaTokenizer")


def _load_stopwords(path: Path | None) -> list[str]:
    if path is None:
        try:
            from nltk.corpus import stopwords

            values = list(stopwords.words("english"))
        except (ImportError, LookupError, OSError) as exc:
            _fail(
                "local NLTK English stopwords are unavailable; supply --stopwords-file "
                f"without downloading data during conversion ({exc})"
            )
    else:
        source = _resolve_file(path, "stopwords file")
        text = source.read_text(encoding="utf-8")
        if source.suffix.lower() == ".json":
            try:
                values = json.loads(text)
            except json.JSONDecodeError as exc:
                _fail(f"stopwords JSON is invalid ({exc})")
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                _fail("stopwords JSON must be an array of strings")
        else:
            values = [line.strip() for line in text.splitlines() if line.strip()]
    if any(not word or word != word.lower() for word in values):
        _fail("stopwords must be non-empty lowercase strings")
    if len(values) != len(set(values)):
        _fail("stopwords must not contain duplicates")
    return sorted(values)


def _load_punkt_parameters(path: Path | None) -> dict[str, object]:
    if path is not None:
        source = _resolve_file(path, "Punkt parameters file")
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail(f"Punkt parameters JSON is invalid ({exc})")
        if not isinstance(value, dict):
            _fail("Punkt parameters JSON must be an object")
        return value
    try:
        import nltk.data

        sentence_tokenizer = nltk.data.load("tokenizers/punkt/english.pickle")
        parameters = sentence_tokenizer._params
    except (ImportError, LookupError, OSError, AttributeError) as exc:
        _fail(
            "local NLTK Punkt English parameters are unavailable; supply "
            f"--punkt-parameters-json without downloading data during conversion ({exc})"
        )
    return {
        "abbreviations": sorted(parameters.abbrev_types),
        "collocations": [list(item) for item in sorted(parameters.collocations)],
        "sentence_starters": sorted(parameters.sent_starters),
        "orthographic_context": dict(sorted(parameters.ortho_context.items())),
    }


def _load_trusted_checkpoint(torch, checkpoint: Path, legacy_code_dirs: list[Path]):
    for directory in reversed(legacy_code_dirs):
        trusted_directory = _resolve_directory(directory, "legacy code directory")
        sys.path.insert(0, str(trusted_directory))
    print(
        "WARNING: loading a full-object PyTorch pickle. This can execute arbitrary code.\n"
        "Proceeding only because --i-understand-pickle-is-unsafe was supplied.\n"
        "The checkpoint and every --legacy-code-dir must be trusted.",
        file=sys.stderr,
    )
    kwargs: dict[str, Any] = {"map_location": torch.device("cpu")}
    if "weights_only" in inspect.signature(torch.load).parameters:
        kwargs["weights_only"] = False
    try:
        with checkpoint.open("rb") as checkpoint_file:
            model = torch.load(checkpoint_file, **kwargs)
    except Exception as exc:
        _fail(
            "trusted checkpoint loading failed; run this script with the legacy model class and "
            f"its original dependencies importable ({exc})"
        )
    if not isinstance(model, torch.nn.Module):
        _fail("legacy checkpoint must contain a torch.nn.Module saved with torch.save(model)")
    if not callable(getattr(model, "get_logits", None)):
        _fail("legacy model must provide get_logits(text_input_ids, code_input_ids)")
    return model.to(device="cpu").eval()


def _assert_logits(torch, value, batch_size: int, label: str):
    if not isinstance(value, torch.Tensor):
        _fail(f"{label} must return one tensor")
    if tuple(value.shape) != (batch_size, 2):
        _fail(f"{label} must return raw logits with shape [{batch_size}, 2]")
    if not torch.is_floating_point(value):
        _fail(f"{label} raw logits must be floating point")
    if not bool(torch.isfinite(value).all()):
        _fail(f"{label} returned a non-finite raw logit")
    return value.detach().to(device="cpu", dtype=torch.float32)


def _export_torchscript(torch, model, path: Path, special_ids: dict[str, int]) -> None:
    class LegacyLogitsWrapper(torch.nn.Module):
        def __init__(self, legacy_model):
            super().__init__()
            self.legacy_model = legacy_model

        def forward(self, text_input_ids, code_input_ids):
            logits = self.legacy_model.get_logits(text_input_ids, code_input_ids)
            return logits.to(dtype=torch.float32)

    def inputs(batch_size: int):
        text = torch.full((batch_size, 512), special_ids["pad_token_id"], dtype=torch.long)
        code = torch.full((batch_size, 512), special_ids["pad_token_id"], dtype=torch.long)
        for tensor in (text, code):
            tensor[:, 0] = special_ids["cls_token_id"]
            tensor[:, 1] = special_ids["sep_token_id"]
            tensor[:, 2] = special_ids["sep_token_id"]
        return text, code

    wrapper = LegacyLogitsWrapper(model).eval()
    trace_inputs = inputs(2)
    with torch.inference_mode():
        reference = _assert_logits(torch, wrapper(*trace_inputs), 2, "legacy get_logits")
        try:
            traced = torch.jit.trace(wrapper, trace_inputs, check_trace=False, strict=True)
            frozen = torch.jit.freeze(traced.eval())
        except Exception as exc:
            _fail(f"TorchScript tracing failed ({exc})")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as model_file:
        torch.jit.save(frozen, model_file)
    try:
        with path.open("rb") as model_file:
            deployed = torch.jit.load(model_file, map_location=torch.device("cpu")).eval()
    except Exception as exc:
        _fail(f"the exported TorchScript artifact cannot be loaded on CPU ({exc})")
    with torch.inference_mode():
        actual = _assert_logits(torch, deployed(*trace_inputs), 2, "exported TorchScript")
        torch.testing.assert_close(actual, reference, rtol=1e-4, atol=1e-5)
        for batch_size in (1, 3):
            batch_inputs = inputs(batch_size)
            expected = _assert_logits(
                torch, wrapper(*batch_inputs), batch_size, "legacy get_logits"
            )
            observed = _assert_logits(
                torch, deployed(*batch_inputs), batch_size, "exported TorchScript"
            )
            torch.testing.assert_close(observed, expected, rtol=1e-4, atol=1e-5)


def _validate_training_metadata(args: argparse.Namespace) -> dict[str, object]:
    metadata: dict[str, object] = {"method": "ACCS"}
    if args.training_code_commit:
        metadata["code_commit"] = args.training_code_commit
    has_temperature = args.training_temperature is not None
    has_split = args.training_temperature_fit_split_sha256 is not None
    if has_temperature != has_split:
        _fail("--training-temperature and --training-temperature-fit-split-sha256 are paired")
    if has_temperature:
        if not math.isfinite(args.training_temperature) or args.training_temperature <= 0:
            _fail("--training-temperature must be positive and finite")
        digest = args.training_temperature_fit_split_sha256
        if not SHA256_RE.fullmatch(digest or ""):
            _fail("training temperature fit split SHA-256 must contain 64 hexadecimal characters")
        metadata["training_temperature"] = {
            "name": "T_train",
            "value": args.training_temperature,
            "fit_split_sha256": digest.lower(),
            "deployment_use": "prohibited",
        }
    return metadata


def _model_scope_metadata(
    project_key_strategy: str,
    fixed_project_key: str | None,
) -> dict[str, object]:
    """Return a conservative, machine-readable scope declaration.

    An issue-key-prefix package can derive a key at inference time, but the
    exporter must not pretend that every resulting project was validated.
    Therefore its enumerated key list is intentionally empty.
    """
    if project_key_strategy == "fixed":
        if not fixed_project_key:
            _fail("a fixed project-key strategy requires a project key")
        if fixed_project_key != fixed_project_key.upper():
            _fail("--fixed-project-key must use uppercase project-key spelling")
        return {
            "model_scope": "project",
            "project_key_strategy": "fixed",
            "supported_project_keys": [fixed_project_key],
        }
    return {
        "model_scope": "issue_key_prefix",
        "project_key_strategy": "issue-key-prefix",
        "supported_project_keys": [],
    }


def _build_package(args: argparse.Namespace, staging: Path) -> str:
    try:
        import torch
    except (ImportError, OSError) as exc:
        _fail(f"PyTorch is required in the trusted conversion environment ({exc})")

    checkpoint = _resolve_file(args.checkpoint, "legacy checkpoint")
    tokenizer_path = staging / "tokenizer" / "tokenizer.json"
    tokenizer, special_ids = _prepare_tokenizer(args.tokenizer, tokenizer_path)
    del tokenizer

    if args.max_batch_size < 1 or args.max_batch_size > 512:
        _fail("--max-batch-size must be between 1 and 512")
    if args.project_key_strategy == "fixed":
        if not args.fixed_project_key:
            _fail("--fixed-project-key is required when --project-key-strategy=fixed")
        if args.fixed_project_key != args.fixed_project_key.upper():
            _fail("--fixed-project-key must use uppercase project-key spelling")
    elif args.fixed_project_key is not None:
        _fail("--fixed-project-key must be omitted for issue-key-prefix")

    try:
        nltk_version = importlib.metadata.version("nltk")
        tokenizers_version = importlib.metadata.version("tokenizers")
    except importlib.metadata.PackageNotFoundError as exc:
        _fail(f"a preprocessing dependency is missing ({exc})")
    preprocessing = {
        "preprocess_format": PREPROCESS_FORMAT,
        "format_version": PREPROCESS_FORMAT_VERSION,
        "tokenizer_format": "huggingface-tokenizers-json",
        "max_sequence_length": 512,
        "truncation": "legacy-balanced-pair-v1",
        **special_ids,
        "project_key_strategy": args.project_key_strategy,
        "fixed_project_key": args.fixed_project_key,
        "stopwords": _load_stopwords(args.stopwords_file),
        "sentence_tokenizer": "nltk-punkt-parameters-v1",
        "word_tokenizer": "nltk-treebank-v1",
        "stemmer": "nltk-snowball-english-v1",
        "required_nltk_version": nltk_version,
        "required_tokenizers_version": tokenizers_version,
        "punkt_parameters": _load_punkt_parameters(args.punkt_parameters_json),
    }
    preprocessing_path = staging / "preprocessing.json"
    _canonical_json(preprocessing_path, preprocessing)

    model = _load_trusted_checkpoint(torch, checkpoint, args.legacy_code_dir)
    model_path = staging / "model" / "model.ts"
    _export_torchscript(torch, model, model_path, special_ids)

    metadata = {
        "architecture": "legacy-accs-dual-encoder",
        "legacy_checkpoint_sha256": _sha256(checkpoint),
        "legacy_checkpoint_format": "torch-save-full-object-pickle",
        "exported_entrypoint": "get_logits",
        **_model_scope_metadata(args.project_key_strategy, args.fixed_project_key),
        "training": _validate_training_metadata(args),
        "deployment_calibration_included": False,
    }
    manifest = {
        "package_format": PACKAGE_FORMAT,
        "format_version": PACKAGE_FORMAT_VERSION,
        "model_name": args.model_name.strip(),
        "model_version": args.model_version.strip(),
        "source_domain": args.source_domain.strip(),
        "metadata": metadata,
        "producer_torch_version": str(torch.__version__),
        "model_file": "model/model.ts",
        "model_sha256": _sha256(model_path),
        "tokenizer_file": "tokenizer/tokenizer.json",
        "tokenizer_sha256": _sha256(tokenizer_path),
        "preprocessing_file": "preprocessing.json",
        "preprocessing_sha256": _sha256(preprocessing_path),
        "model_signature": MODEL_SIGNATURE,
        "preprocessing": PREPROCESS_FORMAT,
        "max_batch_size": args.max_batch_size,
        "positive_class_index": 1,
    }
    manifest_path = staging / "manifest.json"
    _canonical_json(manifest_path, manifest)

    repository_root = Path(__file__).resolve().parents[1]
    backend_root = repository_root / "backend"
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    try:
        from app.model_package import validate_model_manifest
    except (ImportError, ModuleNotFoundError) as exc:
        print(
            "NOTICE: ACCS-Trace backend dependencies are not installed in this trusted training "
            "environment, so repository schema validation was skipped. The registration API and "
            f"repository tests must validate the generated package before use ({exc}).",
            file=sys.stderr,
        )
    else:
        try:
            validate_model_manifest(manifest_path.resolve())
        except Exception as exc:
            _fail(f"the generated package failed ACCS-Trace manifest validation ({exc})")

    manifest_sha256 = _sha256(manifest_path)
    checksum_lines = [
        f"{_sha256(model_path)}  model/model.ts",
        f"{_sha256(tokenizer_path)}  tokenizer/tokenizer.json",
        f"{_sha256(preprocessing_path)}  preprocessing.json",
        f"{manifest_sha256}  manifest.json",
    ]
    (staging / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n", encoding="ascii")
    return manifest_sha256


def main() -> int:
    args = _parser().parse_args()
    if not args.i_understand_pickle_is_unsafe:
        print(
            "REFUSING TO LOAD PICKLE: pass --i-understand-pickle-is-unsafe only after "
            "verifying the checkpoint and all legacy Python code are trusted.",
            file=sys.stderr,
        )
        return 2
    output = args.output_dir.expanduser().resolve()
    if output.exists():
        _fail(f"output directory already exists; choose a new directory: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        manifest_sha256 = _build_package(args, staging)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"Created ACCS TorchScript package: {output}")
    print(f"manifest_sha256={manifest_sha256}")
    print("The package contains raw logits only. Register T_deploy separately after validation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
