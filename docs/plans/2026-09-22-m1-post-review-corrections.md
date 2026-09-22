# M1 post-review corrections

The final whole-branch review of `docs/plans/2026-09-22-m1-data-pipeline.md` returned
**With fixes**: three Important issues that are closed here, and one carried forward
to M2.

This addendum supersedes the corresponding task blocks in the M1 plan. The task
blocks remain the record of how the milestone was built.

| Issue | Disposition |
|---|---|
| C1 `validate` raises on an invalid-UTF-8 artifact | Fixed here |
| C2 `build_database` does not validate its own output | Fixed here |
| C3 `fetch` neither records nor verifies sha256 | Fixed here |
| C4 the latency gate never measures a short prefix | Carried forward to M2 (see below) |

---

## C1. `validate` must not raise on an invalid-UTF-8 artifact

`artifact.decode` compares names as raw bytes and never decodes them, so an artifact
whose `names` blob holds invalid UTF-8 decodes cleanly and then raises
`UnicodeDecodeError` out of `validate`'s semantic loop. Spec §11.3 item 8 lists
invalid UTF-8 as a validation check, and the module's contract is that `validate`
returns error strings and never raises.

In `build/validate_database.py`, replace the tag loop and the alias loop inside
`validate` with:

```python
    deprecated_count = 0
    tag_names: set[str] = set()
    for index in range(artifact.n_tags):
        try:
            name = artifact.name(index)
        except UnicodeDecodeError:
            errors.append(f"tag {index}: name is not valid UTF-8")
            name = "<invalid utf-8>"
        else:
            tag_names.add(name)
            if not name.strip():
                errors.append(f"tag {index}: empty name")
        if artifact.category(index) not in VALID_CATEGORIES:
            errors.append(f"tag {index} ({name}): invalid category {artifact.category(index)}")
        if artifact.post_count(index) > post_count_limit:
            errors.append(f"tag {index} ({name}): implausible post_count {artifact.post_count(index)}")
        if artifact.deprecated(index):
            deprecated_count += 1

    for index in range(artifact.n_aliases):
        try:
            alias = artifact.alias(index)
        except UnicodeDecodeError:
            errors.append(f"alias {index}: name is not valid UTF-8")
            continue
        if alias in tag_names:
            errors.append(f"alias {index} ({alias}): alias name is also a tag name")
        if artifact.deprecated(artifact.alias_target(index)):
            errors.append(f"alias {index} ({alias}): target is deprecated")
```

`tag_names` is populated only in the `else` branch, so an undecodable name is never
compared. The category and post_count checks still run for an undecodable tag, using
the placeholder in the message.

Test to append to `tests/test_validate.py` (add `import struct` at the top of the
module):

```python
def test_invalid_utf8_name_is_reported_not_raised(tmp_path):
    blob = bytearray(encode(TagSet(
        threshold=25,
        tags=(TagEntry("a", 0, 10, False),),
        aliases=(),
        alias_target=(),
    )))
    off_names = struct.unpack_from("<I", blob, 20)[0]
    blob[off_names:off_names + 1] = b"\xff"
    path = tmp_path / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(blob)
    assert any("not valid UTF-8" in error for error in validate(path))


def test_invalid_utf8_alias_is_reported_not_raised(tmp_path):
    blob = bytearray(encode(TagSet(
        threshold=25,
        tags=(TagEntry("a", 0, 10, False),),
        aliases=("b",),
        alias_target=(0,),
    )))
    off_alias_names = struct.unpack_from("<I", blob, 40)[0]
    blob[off_alias_names:off_alias_names + 1] = b"\xff"
    path = tmp_path / "tags.bin.gz"
    with open(path, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as stream:
            stream.write(blob)
    assert any("not valid UTF-8" in error for error in validate(path))
```

A single-byte replacement keeps every byte-order invariant intact (one name, one
alias), so `decode` still accepts the buffer.

---

## C2. `build_database` must validate its own output and clean up on failure

Spec §11.2: after generating the artifacts, call the validator internally; on failure
remove the artifacts and signal failure. Today `main` writes them and exits zero,
leaving an unverified artifact behind if the CLI gate is ever skipped.

In `build/build_database.py`:

1. add `from validate_database import validate` beside the other `build` imports at
   module scope (`validate_database` imports only `artifact`, so there is no cycle);
2. in `main`, after the `write_artifacts(...)` call and before the stats print:

```python
    artifact_path = Path(args.out) / "tags.bin.gz"
    metadata_path = Path(args.out) / "metadata.json"
    errors = validate(artifact_path, metadata_path)
    if errors:
        artifact_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        for error in errors:
            print(f"error: {error}")
        return 1
```

`main` already returns an `int` that `__main__` passes to `SystemExit`, so returning
`1` is the process-level failure the spec asks for; nothing is left on disk.

Test to append to `tests/test_build.py` (it already has `hlibr_data()`):

```python
def test_main_removes_outputs_when_validation_fails(tmp_path, monkeypatch):
    from build import build_database

    class StubSource:
        def read(self, fetched):
            return hlibr_data()

    monkeypatch.setattr(build_database, "fetch_all", lambda names, cache: [object()])
    monkeypatch.setattr(build_database, "SOURCE_ORDER", ("hlibr",))
    monkeypatch.setattr(build_database, "SOURCES", {"hlibr": StubSource})
    monkeypatch.setattr(build_database, "validate", lambda artifact, metadata: ["synthetic failure"])

    out = tmp_path / "generated"
    code = build_database.main(["--profile", str(FIXTURE_PROFILE), "--out", str(out)])

    assert code == 1
    assert not (out / "tags.bin.gz").exists()
    assert not (out / "metadata.json").exists()
```

Add near the top of `tests/test_build.py`:

```python
FIXTURE_PROFILE = "profiles/danbooru.yaml"
```

---

## C3. `fetch` must record and verify each file's sha256

Spec §11.1 requires recording the sha256 of every downloaded file. Hugging Face's
tree API exposes each LFS file's sha256 as `lfs.oid`, which doubles as the expected
hash, so this also closes the silent stale-cache path: `download` returns any
existing destination unchecked when no expected hash is supplied, and both adapters
supply none today.

In `build/sources/base.py`:

1. import `field` from `dataclasses`;
2. give `FetchResult` a hashes field (a default keeps the existing positional
   constructions in the adapter tests working):

```python
@dataclass(frozen=True)
class FetchResult:
    source_id: str
    revision: str
    data_date: str
    files: dict[str, Path]
    hashes: dict[str, str] = field(default_factory=dict)
```

3. add a helper next to `hf_dataset_tree`:

```python
def hf_lfs_oids(repo_id: str, revision: str) -> dict[str, str]:
    """Map each LFS file path to the sha256 the Hub published for it."""
    return {
        entry["path"]: entry["lfs"]["oid"]
        for entry in hf_dataset_tree(repo_id, revision)
        if isinstance(entry.get("lfs"), dict) and entry["lfs"].get("oid")
    }
```

4. verify a fresh download in `download`, immediately after `os.replace`:

```python
    digest = sha256_of(destination)
    if expected_sha256 is not None and digest != expected_sha256:
        temporary.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise ValueError(f"{url}: sha256 mismatch (expected {expected_sha256}, got {digest})")
    return destination
```

In `build/sources/hlibr.py`'s `fetch`: obtain `oids = hf_lfs_oids(self.id, revision)`
once, pass `expected_sha256=oids.get(name)` to each `download`, and build the result
with `hashes={name: sha256_of(path) for name, path in files.items()}`.

In `build/sources/hdiffusion.py`'s `fetch`: do the same for the single CSV, keyed by
the file path as it appears in the tree.

In `build/fetch_upstream.py`'s `main`, add `"hashes": result.hashes` to each manifest
entry.

Tests to append to `tests/test_sources.py` (add the imports the fixture needs):

```python
import functools
import hashlib
import http.server
import threading


@pytest.fixture
def http_files(tmp_path):
    root = tmp_path / "served"
    root.mkdir()

    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield root, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_download_accepts_a_matching_sha256(http_files):
    root, base_url = http_files
    payload = b"payload"
    (root / "data.bin").write_bytes(payload)
    destination = root / "cache" / "data.bin"

    download(f"{base_url}/data.bin", destination, expected_sha256=hashlib.sha256(payload).hexdigest())

    assert destination.read_bytes() == payload


def test_download_rejects_a_sha256_mismatch(http_files):
    root, base_url = http_files
    (root / "data.bin").write_bytes(b"payload")
    destination = root / "cache" / "data.bin"

    with pytest.raises(ValueError, match="sha256 mismatch"):
        download(f"{base_url}/data.bin", destination, expected_sha256="0" * 64)

    assert not destination.exists()
```

Then re-run the real fetch against the existing cache to exercise the Hub's LFS oids
end to end (no large download; the files are already in `data/raw/`):

```bash
.venv/bin/python build/fetch_upstream.py --cache data/raw
```

---

## C4. Carried forward to M2 — the latency gate is not representative

The gate measures only long prefixes (~0.3ms each). Measured on the real artifact
built by Task 9 (193,803 tags), the queries the frontend will actually issue are far
more expensive:

| query | latency |
|---|---|
| `a` | 40.7 ms |
| `b` | 24.3 ms |
| `blue` | 0.96 ms |
| `hatsune_mik` | 0.30 ms |

Spec §8.1 says search starts at one character, so a one-character prefix is the
common case, and 40.7 ms sits just under the 50 ms budget on this machine.

The cause is structural rather than incidental: `TagIndex._collect` materialises every
prefix match into a `SearchHit` list before `search` slices to `limit`, so cost grows
with the number of matches rather than with the number returned. The synthetic
full-size benchmark cannot measure this honestly either — its names all share the
`tag_` prefix, so a one-character query would match all 1,710,000 entries and fail for
a reason that does not reflect real data.

Carried forward, not fixed here, because closing it properly means changing a reviewed
algorithm (bounded top-k selection that never materialises more than it needs) and
that deserves its own task and review rather than an unreviewed edit to `artifact.py`.

M2 must do both:

1. reduce the per-query cost so a one-character prefix is comfortably inside budget
   on the default profile, then re-measure;
2. make the gate represent real queries — either a short-prefix case over a synthetic
   set whose first character is distributed, or a real-artifact case that skips when
   `generated/tags.bin.gz` is absent.

Until then, the default 50 ms budget applies to the long-prefix workload only.
