"""Standalone TorchScript compatibility probe; requires only PyTorch."""

from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import torch


class TinyContractModel(torch.nn.Module):
    def forward(self, text_input_ids, code_input_ids):
        evidence = (text_input_ids == 8).sum(dim=1) + (code_input_ids == 8).sum(dim=1)
        evidence = evidence.to(dtype=torch.float32)
        return torch.stack((-evidence, evidence), dim=1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        package_root = Path(temporary_directory) / "模型包"
        package_root.mkdir()
        model_path = package_root / "model.ts"
        example_text = torch.zeros((2, 512), dtype=torch.long)
        example_code = torch.zeros((2, 512), dtype=torch.long)
        module = torch.jit.trace(
            TinyContractModel().eval(),
            (example_text, example_code),
            check_trace=False,
        )
        with model_path.open("wb") as model_file:
            torch.jit.save(module, model_file)
        with model_path.open("rb") as model_file:
            loaded = torch.jit.load(model_file, map_location=torch.device("cpu"))
        loaded.eval()

        text_input_ids = torch.zeros((3, 512), dtype=torch.long)
        code_input_ids = torch.zeros((3, 512), dtype=torch.long)
        text_input_ids[0, 0] = 8
        code_input_ids[2, 0] = 8
        with torch.inference_mode():
            logits = loaded(text_input_ids, code_input_ids)
        assert logits.shape == (3, 2)
        assert torch.is_floating_point(logits)
        assert bool(torch.isfinite(logits).all())
        probabilities = torch.softmax(logits, dim=1)[:, 1]
        assert bool(((probabilities >= 0) & (probabilities <= 1)).all())
        print(
            json.dumps(
                {
                    "status": "ok",
                    "torch_version": torch.__version__,
                    "device": str(logits.device),
                    "model_sha256": sha256_file(model_path),
                    "scores": [float(value) for value in probabilities.tolist()],
                },
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
