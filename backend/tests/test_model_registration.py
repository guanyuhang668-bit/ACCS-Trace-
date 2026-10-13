from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import models as models_api
from app.model_package import PackageUnavailable


def _package(tmp_manifest: Path):
    digest = "a" * 64
    return SimpleNamespace(
        name="ACCS Issue-Commit",
        version="2.0.0",
        source_domain="Apache projects",
        runtime_kind="accs_torchscript_v1",
        package_checksum_sha256=digest,
        manifest_sha256=digest,
        manifest_path=tmp_manifest.resolve(),
        metadata={
            "training_method": "ACCS",
            "model_scope": "project",
            "project_key_strategy": "fixed",
            "supported_project_keys": ["TIKA"],
        },
        manifest=SimpleNamespace(
            model_signature=(
                "forward(text_input_ids:int64[B,512],code_input_ids:int64[B,512])"
                "->raw_logits:float[B,2]"
            ),
            preprocessing="legacy_accs_roberta_bpe_v1",
            producer_torch_version="2.5.1",
            max_batch_size=16,
        ),
    )


@pytest.mark.parametrize(
    "package_name",
    [
        "../outside",
        "nested/package",
        r"nested\package",
        r"C:\models\accs",
        "/absolute",
        "..",
        "accs..v2",
        ".hidden",
        "accs.v2",
        " accs",
    ],
)
def test_registration_rejects_non_directory_names(client, package_name: str) -> None:
    response = client.post("/api/models/register", json={"package_name": package_name})

    assert response.status_code == 422
    assert len(client.get("/api/models").json()) == 1


def test_registration_requires_existing_direct_package(client) -> None:
    response = client.post("/api/models/register", json={"package_name": "missing_package"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_registration_rejects_extra_path_fields(client) -> None:
    response = client.post(
        "/api/models/register",
        json={"package_name": "safe_name", "model_path": "C:/outside/model.ts"},
    )

    assert response.status_code == 422
    assert len(client.get("/api/models").json()) == 1


def test_registers_validated_cpu_runtime_without_calibration(
    client,
    settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = settings.models_dir / "accs_release"
    package_root.mkdir()
    manifest_path = package_root / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    package = _package(manifest_path)
    runtime_checks = []

    def fake_validate(path: Path):
        assert Path(path).resolve() == manifest_path.resolve()
        return package

    def fake_runtime(release):
        runtime_checks.append(release)
        assert Path(release.model_path).is_absolute()
        assert release.checksum_sha256 == package.manifest_sha256
        assert release.runtime_kind == "accs_torchscript_v1"
        return object()

    monkeypatch.setattr(models_api, "validate_model_manifest", fake_validate)
    monkeypatch.setattr(models_api, "get_runtime", fake_runtime)

    response = client.post(
        "/api/models/register",
        json={"package_name": "accs_release"},
    )

    assert response.status_code == 201, response.text
    model = response.json()
    assert model["name"] == "ACCS Issue-Commit"
    assert model["version"] == "2.0.0"
    assert model["runtime_kind"] == "accs_torchscript_v1"
    assert model["package_path"] == "models/accs_release"
    assert model["manifest_path"] == "models/accs_release/manifest.json"
    assert model["checksum_sha256"] == "a" * 64
    assert model["metadata_json"] == {
        "package_path": "models/accs_release",
        "manifest_path": "models/accs_release/manifest.json",
        "manifest_sha256": "a" * 64,
        "package_format": package.runtime_kind,
        "input_schema": package.manifest.model_signature,
        "preprocessing": package.manifest.preprocessing,
        "producer_torch_version": package.manifest.producer_torch_version,
        "max_batch_size": package.manifest.max_batch_size,
        "package_metadata": package.metadata,
        "model_scope": "project",
        "project_key_strategy": "fixed",
        "supported_project_keys": ["TIKA"],
    }
    assert model["calibration_profiles"] == []
    assert str(settings.data_dir.resolve()) not in response.text
    assert len(runtime_checks) == 1

    listed = client.get("/api/models").json()
    assert len(listed) == 2
    assert listed[0] == model

    duplicate = client.post(
        "/api/models/register",
        json={"package_name": "accs_release"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"
    assert len(runtime_checks) == 1


@pytest.mark.parametrize("failure_stage", ["manifest", "runtime"])
def test_failed_package_validation_never_persists_release(
    client,
    settings,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    package_root = settings.models_dir / "broken_release"
    package_root.mkdir()
    manifest_path = package_root / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    package = _package(manifest_path)

    if failure_stage == "manifest":
        def reject_manifest(_path: Path):
            raise PackageUnavailable("The ACCS package manifest is invalid")

        monkeypatch.setattr(models_api, "validate_model_manifest", reject_manifest)
    else:
        monkeypatch.setattr(models_api, "validate_model_manifest", lambda _path: package)

        def reject_runtime(_release):
            raise PackageUnavailable("The ACCS CPU runtime dependency is unavailable")

        monkeypatch.setattr(models_api, "get_runtime", reject_runtime)

    response = client.post(
        "/api/models/register",
        json={"package_name": "broken_release"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "model_runtime_unavailable"
    assert len(client.get("/api/models").json()) == 1
