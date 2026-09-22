"""Validate a built artifact before it is released."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from artifact import VALID_CATEGORIES, decode, load_custom  # noqa: E402


def read_artifact(path: Path) -> bytes:
    data = path.read_bytes()
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    return data


def validate(
    artifact_path: Path,
    metadata_path: Path | None = None,
    post_count_limit: int = 50_000_000,
) -> list[str]:
    errors: list[str] = []
    try:
        raw = read_artifact(artifact_path)
    except (OSError, gzip.BadGzipFile) as exc:
        return [f"artifact could not be read: {exc}"]

    try:
        artifact = decode(raw)
    except ValueError as exc:
        return [f"artifact decode failed: {exc}"]

    for index in range(artifact.n_tags):
        name = artifact.name(index)
        if not name.strip():
            errors.append(f"tag {index}: empty name")
        if artifact.category(index) not in VALID_CATEGORIES:
            errors.append(f"tag {index} ({name}): invalid category {artifact.category(index)}")
        if artifact.post_count(index) > post_count_limit:
            errors.append(f"tag {index} ({name}): implausible post_count {artifact.post_count(index)}")

    tag_names = {artifact.name(index) for index in range(artifact.n_tags)}
    for index in range(artifact.n_aliases):
        alias = artifact.alias(index)
        if alias in tag_names:
            errors.append(f"alias {index} ({alias}): alias name is also a tag name")
        if artifact.deprecated(artifact.alias_target(index)):
            errors.append(f"alias {index} ({alias}): target is deprecated")

    if metadata_path is not None and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        counts = metadata.get("counts", {})
        if counts.get("tags") != artifact.n_tags:
            errors.append(f"metadata counts.tags {counts.get('tags')} != {artifact.n_tags}")
        if counts.get("aliases") != artifact.n_aliases:
            errors.append(f"metadata counts.aliases {counts.get('aliases')} != {artifact.n_aliases}")
        if metadata.get("threshold") != artifact.threshold:
            errors.append(f"metadata threshold {metadata.get('threshold')} != {artifact.threshold}")
        digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if metadata.get("artifact", {}).get("sha256") != digest:
            errors.append("metadata artifact.sha256 does not match the file")
    return errors


def validate_custom(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"custom file could not be read: {exc}"]
    try:
        result = load_custom(text, path.name)
    except ValueError as exc:
        return [f"custom file parse error: {exc}"]
    return list(result.warnings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a built tag artifact")
    parser.add_argument("--artifact", default="generated/tags.bin.gz")
    parser.add_argument("--metadata", default="generated/metadata.json")
    parser.add_argument("--custom", action="append", dest="custom_files", default=[])
    args = parser.parse_args(argv)

    metadata_path = Path(args.metadata)
    errors = validate(Path(args.artifact), metadata_path if metadata_path.exists() else None)
    for custom_path in args.custom_files:
        for warning in validate_custom(Path(custom_path)):
            print(f"warning: {custom_path}: {warning}")

    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print("validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
