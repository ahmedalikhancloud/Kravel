"""Build-time public model download; inference is offline, safetensors-only."""
import hashlib
import json
from pathlib import Path

MODELS = {
    "embedding": {"repo": "sentence-transformers/all-MiniLM-L6-v2", "revision": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"},
    "reranker": {"repo": "cross-encoder/ms-marco-MiniLM-L6-v2", "revision": "233902d25c440f23af6f7d6e94d2946bac0bee0a"},
}


def download(root="/models"):
    from huggingface_hub import snapshot_download
    for role, spec in MODELS.items():
        directory = Path(root) / role
        snapshot_download(repo_id=spec["repo"], revision=spec["revision"], local_dir=str(directory),
            allow_patterns=["*.json", "*.safetensors", "vocab.txt", "README.md"],
            ignore_patterns=["onnx/*", "openvino/*", ".cache/*"], max_workers=2)
        artifacts = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file() and ".cache" not in p.parts}
        if "model.safetensors" not in artifacts:
            raise ValueError("Pinned model has no safe tensor weights")
        (directory / "kravel-artifact.json").write_text(json.dumps({**spec, "license": "Apache-2.0", "sha256": artifacts}, indent=2))
        print(f"Verified public {role} artifact: {spec['repo']} @ {spec['revision']}")


def verify(directory):
    directory = Path(directory)
    spec = json.loads((directory / "kravel-artifact.json").read_text())
    if not spec.get("sha256"):
        raise ValueError("Missing artifact hashes")
    for relative, expected in spec["sha256"].items():
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Local model artifact integrity check failed")
    return spec


if __name__ == "__main__":
    download()
