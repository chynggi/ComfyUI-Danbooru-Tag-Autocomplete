"""Shared types and HTTP helpers for upstream tag sources."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests


@dataclass(frozen=True, slots=True)
class TagRecord:
    name: str
    category: int
    post_count: int
    is_deprecated: bool
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class FetchResult:
    source_id: str
    revision: str
    data_date: str
    files: dict[str, Path]


@dataclass(frozen=True)
class SourceData:
    source_id: str
    revision: str
    data_date: str
    tags: dict[str, TagRecord]
    aliases: dict[str, str]


class SourceAdapter(Protocol):
    id: str

    def fetch(self, cache_dir: Path) -> FetchResult: ...

    def read(self, fetched: FetchResult) -> SourceData: ...


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hf_dataset_revision(repo_id: str) -> str:
    response = requests.get(f"https://huggingface.co/api/datasets/{repo_id}", timeout=30)
    response.raise_for_status()
    return response.json()["sha"]


def hf_dataset_tree(repo_id: str, revision: str) -> list[dict]:
    response = requests.get(
        f"https://huggingface.co/api/datasets/{repo_id}/tree/{revision}",
        params={"recursive": "true"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def hf_resolve_url(repo_id: str, revision: str, path: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}/resolve/{revision}/{path}"


def download(url: str, destination: Path, expected_sha256: str | None = None) -> Path:
    """Download to `destination`, skipping work when the cached file already matches."""
    if destination.exists():
        if expected_sha256 is None or sha256_of(destination) == expected_sha256:
            return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    with requests.get(url, stream=True, timeout=300) as response:
        response.raise_for_status()
        with open(temporary, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
    os.replace(temporary, destination)
    return destination
