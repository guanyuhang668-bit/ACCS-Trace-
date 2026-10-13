from __future__ import annotations

"""Project/model compatibility rules shared by registration, runs, and APIs.

Compatibility is deliberately based on a small, explicit contract.  A model
package may declare a fixed project scope (``model_scope=project`` and a
non-empty ``supported_project_keys`` list) or a dynamic Issue-key-prefix scope
(``model_scope=issue_key_prefix`` and an empty list).  Older packages can be
understood from their frozen legacy preprocessing metadata.  We never guess a
project key from a free-form ``source_domain`` string.
"""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .errors import ValidationError
from .model_package import (
    LoadedModelPackage,
    PackageUnavailable,
    validate_model_manifest,
)
from .models import ModelRelease, Project


PROJECT_KEY_PATTERN = re.compile(r"[A-Z0-9_#]+(?:/[A-Z0-9_#]+)*")
ISSUE_KEY_PREFIX_PATTERN = re.compile(r"[A-Z0-9_#]+")
MODEL_SCOPE_PROJECT = "project"
MODEL_SCOPE_ISSUE_KEY_PREFIX = "issue_key_prefix"
PROJECT_KEY_STRATEGY_FIXED = "fixed"
PROJECT_KEY_STRATEGY_PREFIX = "issue-key-prefix"


class CompatibilityMetadataError(ValueError):
    """Raised when a model's declared compatibility metadata is malformed."""


@dataclass(frozen=True, slots=True)
class ModelCompatibility:
    """Normalized compatibility declaration.

    ``supported_project_keys=None`` means that no declaration was available.
    An empty tuple means an explicit dynamic prefix scope and therefore accepts
    any valid project key.  A tuple containing ``*`` is an explicit wildcard
    retained for older/demo releases.
    """

    model_scope: str | None
    project_key_strategy: str | None
    supported_project_keys: tuple[str, ...] | None
    source: str

    @property
    def declared(self) -> bool:
        return self.model_scope is not None and self.supported_project_keys is not None

    @property
    def is_dynamic(self) -> bool:
        return self.model_scope == MODEL_SCOPE_ISSUE_KEY_PREFIX


def normalize_project_key(value: str | None) -> str | None:
    """Return the canonical uppercase project key or ``None`` for old rows."""

    if value is None:
        return None
    normalized = value.strip().upper()
    if (
        not normalized
        or len(normalized) > 255
        or not PROJECT_KEY_PATTERN.fullmatch(normalized)
    ):
        raise ValueError(
            "project_key must contain one or more safe project identifiers "
            "separated by '/'"
        )
    segments = normalized.split("/")
    if len(segments) != len(set(segments)):
        raise ValueError("project_key must not contain duplicate project identifiers")
    # Legacy ACCS treats slash-separated keys as an unordered list of tags to
    # remove, so a stable order avoids false model/project mismatches.
    return "/".join(sorted(segments))


def infer_project_key_from_issue_keys(issue_keys: list[str]) -> str | None:
    """Infer a stable project key when all Issue_KEY values expose a prefix."""

    prefixes: set[str] = set()
    for issue_key in issue_keys:
        value = issue_key.strip()
        if "-" not in value:
            return None
        prefix = value.split("-", 1)[0].strip().upper()
        if not prefix or not ISSUE_KEY_PREFIX_PATTERN.fullmatch(prefix):
            return None
        prefixes.add(prefix)
    return "/".join(sorted(prefixes)) if prefixes else None


def ensure_issue_keys_match_project(
    project_key: str | None,
    issue_keys: list[str],
) -> str | None:
    """Infer a missing key or reject issue prefixes outside an explicit key."""

    inferred = infer_project_key_from_issue_keys(issue_keys)
    if project_key is None:
        return inferred
    normalized = normalize_project_key(project_key)
    assert normalized is not None
    if inferred is None:
        raise ValueError(
            "Issue_KEY values must use '<PROJECT>-<NUMBER>' prefixes when project_key is set"
        )
    expected = set(normalized.split("/"))
    observed = set(inferred.split("/"))
    unexpected = sorted(observed - expected)
    if unexpected:
        raise ValueError(
            "Issue_KEY prefixes do not match project_key; unexpected prefixes: "
            + ", ".join(unexpected)
        )
    return normalized


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _metadata_layers(metadata: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if metadata is None:
        return []
    layers = [metadata]
    nested = _mapping(metadata.get("package_metadata"))
    if nested is not None:
        layers.append(nested)
    return layers


def _first_value(layers: list[Mapping[str, Any]], key: str) -> tuple[Any, bool]:
    for layer in layers:
        if key in layer:
            return layer[key], True
    return None, False


def _normalize_supported_keys(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise CompatibilityMetadataError("supported_project_keys must be an array")
    if any(not isinstance(item, str) for item in value):
        raise CompatibilityMetadataError("supported_project_keys must contain strings")
    if "*" in value:
        if len(value) != 1:
            raise CompatibilityMetadataError(
                "the wildcard supported_project_keys declaration must stand alone"
            )
        return ("*",)
    normalized: list[str] = []
    for item in value:
        try:
            key = normalize_project_key(item)
        except ValueError as exc:
            raise CompatibilityMetadataError(str(exc)) from exc
        assert key is not None
        normalized.append(key)
    if len(normalized) != len(set(normalized)):
        raise CompatibilityMetadataError("supported_project_keys must not contain duplicates")
    return tuple(normalized)


def compatibility_from_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    source: str = "metadata",
) -> ModelCompatibility | None:
    """Parse a structured metadata declaration, or return ``None`` if absent."""

    layers = _metadata_layers(metadata)
    if not layers:
        return None
    scope, has_scope = _first_value(layers, "model_scope")
    strategy, has_strategy = _first_value(layers, "project_key_strategy")
    supported, has_supported = _first_value(layers, "supported_project_keys")
    if not (has_scope or has_strategy or has_supported):
        return None

    # A scope object can carry the canonical fields itself.  The layer list
    # already includes it, so values above are resolved consistently.
    if has_scope and not isinstance(scope, str):
        raise CompatibilityMetadataError("model_scope must be 'project' or 'issue_key_prefix'")
    if scope is not None and scope not in {
        MODEL_SCOPE_PROJECT,
        MODEL_SCOPE_ISSUE_KEY_PREFIX,
    }:
        raise CompatibilityMetadataError("model_scope must be 'project' or 'issue_key_prefix'")
    if has_strategy and not isinstance(strategy, str):
        raise CompatibilityMetadataError("project_key_strategy must be a string")
    if strategy is not None and strategy not in {
        PROJECT_KEY_STRATEGY_FIXED,
        PROJECT_KEY_STRATEGY_PREFIX,
    }:
        raise CompatibilityMetadataError(
            "project_key_strategy must be 'fixed' or 'issue-key-prefix'"
        )

    keys: tuple[str, ...] | None
    if has_supported:
        keys = _normalize_supported_keys(supported)
    else:
        keys = None

    if scope is None and strategy is None:
        # A bare supported_project_keys list is intentionally not enough for a
        # production release: the exporter must state whether the model was
        # trained for a fixed project or for dynamic Issue-key prefixes.
        raise CompatibilityMetadataError(
            "a model compatibility declaration requires model_scope or project_key_strategy"
        )
    if scope is None:
        if strategy == PROJECT_KEY_STRATEGY_FIXED:
            scope = MODEL_SCOPE_PROJECT
        elif strategy == PROJECT_KEY_STRATEGY_PREFIX:
            scope = MODEL_SCOPE_ISSUE_KEY_PREFIX
        else:
            raise CompatibilityMetadataError(
                "a model compatibility declaration requires model_scope or project_key_strategy"
            )
    if strategy is None:
        strategy = (
            PROJECT_KEY_STRATEGY_PREFIX
            if scope == MODEL_SCOPE_ISSUE_KEY_PREFIX
            else PROJECT_KEY_STRATEGY_FIXED
        )

    if keys is None:
        raise CompatibilityMetadataError(
            "a model compatibility declaration requires supported_project_keys"
        )
    if scope == MODEL_SCOPE_PROJECT and not keys:
        raise CompatibilityMetadataError(
            "project model scope must declare at least one supported project key"
        )
    if scope == MODEL_SCOPE_ISSUE_KEY_PREFIX and keys not in {(), ("*",)}:
        raise CompatibilityMetadataError(
            "issue_key_prefix model scope must use an empty list or wildcard"
        )
    if scope == MODEL_SCOPE_PROJECT and strategy != PROJECT_KEY_STRATEGY_FIXED:
        raise CompatibilityMetadataError(
            "project model scope must use the fixed project-key strategy"
        )
    if scope == MODEL_SCOPE_ISSUE_KEY_PREFIX and strategy != PROJECT_KEY_STRATEGY_PREFIX:
        raise CompatibilityMetadataError(
            "issue_key_prefix model scope must use the issue-key-prefix strategy"
        )
    return ModelCompatibility(scope, strategy, keys, source)


def _compatibility_from_preprocessing(preprocessing: Any) -> ModelCompatibility:
    if preprocessing.project_key_strategy == PROJECT_KEY_STRATEGY_FIXED:
        try:
            fixed_key = normalize_project_key(preprocessing.fixed_project_key)
        except ValueError as exc:
            raise CompatibilityMetadataError(str(exc)) from exc
        if fixed_key is None:
            raise CompatibilityMetadataError(
                "legacy fixed project-key preprocessing has no fixed_project_key"
            )
        return ModelCompatibility(
            MODEL_SCOPE_PROJECT,
            PROJECT_KEY_STRATEGY_FIXED,
            (fixed_key,),
            "legacy preprocessing",
        )
    return ModelCompatibility(
        MODEL_SCOPE_ISSUE_KEY_PREFIX,
        PROJECT_KEY_STRATEGY_PREFIX,
        (),
        "legacy preprocessing",
    )


def compatibility_from_package(package: LoadedModelPackage) -> ModelCompatibility:
    """Read compatibility and ensure metadata agrees with deployed preprocessing."""

    parsed = compatibility_from_metadata(package.metadata, source="manifest.metadata")
    preprocessing = getattr(package, "preprocessing", None)
    derived = (
        _compatibility_from_preprocessing(preprocessing)
        if preprocessing is not None
        else None
    )
    if parsed is not None:
        if parsed.supported_project_keys and "*" in parsed.supported_project_keys:
            raise CompatibilityMetadataError(
                "real ACCS packages cannot use the supported_project_keys wildcard"
            )
        if derived is not None and (
            parsed.model_scope != derived.model_scope
            or parsed.project_key_strategy != derived.project_key_strategy
            or parsed.supported_project_keys != derived.supported_project_keys
        ):
            raise CompatibilityMetadataError(
                "manifest compatibility metadata does not match preprocessing.json"
            )
        return parsed
    # A small compatibility shim keeps registration unit doubles and old
    # hand-created rows understandable; validated production packages always
    # provide this object.
    if derived is not None:
        return derived
    return ModelCompatibility(None, None, None, "undeclared")


def compatibility_from_model(model: ModelRelease) -> ModelCompatibility:
    """Resolve a release declaration without ever loading a pickle/plugin."""

    if model.is_demo:
        parsed = compatibility_from_metadata(model.metadata_json, source="demo metadata")
        if parsed is not None:
            return parsed
        # Existing demo rows created before this contract remain usable.  The
        # seed now writes an explicit wildcard, but this fallback is harmless
        # and preserves old local databases during migration.
        return ModelCompatibility(
            MODEL_SCOPE_PROJECT,
            PROJECT_KEY_STRATEGY_FIXED,
            ("*",),
            "demo compatibility fallback",
        )

    parsed = compatibility_from_metadata(model.metadata_json, source="release metadata")
    if parsed is not None:
        if parsed.supported_project_keys and "*" in parsed.supported_project_keys:
            raise CompatibilityMetadataError(
                "real ACCS model releases cannot use the supported_project_keys wildcard"
            )
        return parsed

    # A pre-metadata release may still point to a validated manifest.  The
    # validator only reads JSON and hashes; it never deserializes the model.
    if model.model_path:
        try:
            package = validate_model_manifest(model.model_path)
        except (PackageUnavailable, OSError, ValueError):
            package = None
        if package is not None:
            return compatibility_from_package(package)
    return ModelCompatibility(None, None, None, "undeclared")


def safe_compatibility_from_model(model: ModelRelease) -> ModelCompatibility:
    """Best-effort variant for read-only API summaries."""

    try:
        return compatibility_from_model(model)
    except (CompatibilityMetadataError, PackageUnavailable, OSError, ValueError):
        return ModelCompatibility(None, None, None, "invalid")


def ensure_project_model_compatible(project: Project, model: ModelRelease) -> ModelCompatibility:
    """Raise a 422 error unless a run can safely pair project and model."""

    try:
        project_key = normalize_project_key(project.project_key)
    except ValueError as exc:
        raise ValidationError(f"The project has an invalid project_key: {exc}") from exc
    try:
        compatibility = compatibility_from_model(model)
    except CompatibilityMetadataError as exc:
        raise ValidationError(
            f"Model release '{model.name}' version '{model.version}' has invalid "
            f"compatibility metadata: {exc}"
        ) from exc

    if model.is_demo:
        # Demo is intentionally usable for legacy projects with no key.  If a
        # key is supplied, an explicit wildcard or matching declaration still
        # provides useful API documentation without blocking the demo.
        if project_key is None:
            return compatibility
    elif project_key is None:
        raise ValidationError(
            f"A project_key is required before running non-demo model release "
            f"'{model.name}' version '{model.version}'"
        )

    if compatibility.model_scope is None or compatibility.supported_project_keys is None:
        raise ValidationError(
            f"Model release '{model.name}' version '{model.version}' does not declare "
            "supported project keys; re-export the legacy package with compatibility metadata"
        )

    if project_key is None:
        return compatibility
    supported = compatibility.supported_project_keys
    if compatibility.is_dynamic or "*" in supported or project_key in supported:
        return compatibility
    displayed = ", ".join(supported) if supported else "(none)"
    raise ValidationError(
        f"Model release '{model.name}' version '{model.version}' does not support "
        f"project key '{project_key}' (supported project keys: {displayed})"
    )


def compatibility_fields(compatibility: ModelCompatibility) -> dict[str, Any]:
    """Return stable JSON fields for model summaries and run manifests."""

    return {
        "model_scope": compatibility.model_scope,
        "project_key_strategy": compatibility.project_key_strategy,
        "supported_project_keys": (
            list(compatibility.supported_project_keys)
            if compatibility.supported_project_keys is not None
            else None
        ),
    }
