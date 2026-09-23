# M2a post-review corrections

The final whole-branch review of `docs/plans/2026-09-23-m2a-search-runtime.md` returned
**With fixes**: one Critical, four Important, and a deferred-minor triage that promoted two
of the ledger's candidates to must-fix.

This addendum supersedes the corresponding task blocks in the M2a plan. The task blocks
remain the record of how the milestone was built.

| Issue | Disposition |
|---|---|
| C1 `zlib.error` escapes the node and `load_custom` | Fixed here |
| I1 the real-artifact gate can silently skip | Fixed here |
| I2 nothing tests that the package loads the way ComfyUI loads it | Fixed here |
| I3 an overlay entry does not win under a category/deprecated filter | Fixed here |
| I4 the distributed one-character gate's margin is thin on CI | Carried to M3 (see below) |

---

## C1. One owner for "this artifact could not be read"

`gzip.decompress` raises `zlib.error` — not `OSError`, not `ValueError` — when the gzip magic
is intact but the deflate body is corrupt. That escaped `nodes.py`'s catch tuple, so a
damaged cache crashed the workflow instead of returning `""`, and escaped `load_custom`'s, so
`/custom` answered 500. `build/validate_database.py` already had the correct tuple; the
runtime did not. This is the fourth occurrence of the same class in the project, so the
constant moves to the shared layer rather than being copied a fourth time.

In `artifact.py`, add `import gzip` and `import zlib` to the module imports, and put the
constant beside `MAGIC`:

```python
# Failures that mean "the artifact bytes could not be read". gzip.decompress raises
# EOFError on a truncated stream and zlib.error on a corrupt body; neither is an OSError.
READ_ERRORS = (OSError, EOFError, zlib.error, gzip.BadGzipFile)
```

Then:

- `store.py`: keep `from .artifact import Artifact, TagIndex, build_custom_overlay, decode`
  and add `READ_ERRORS`, so `store.READ_ERRORS` is available to the node. Change
  `load_custom`'s catch to:
  ```python
      except (*READ_ERRORS, ValueError, TypeError, csv.Error) as exc:
  ```
- `nodes.py`: import the constant and catch it:
  ```python
  from .artifact import CATEGORY_NAMES, READ_ERRORS, SearchHit, hit_sort_key
  ```
  ```python
          except (*READ_ERRORS, ValueError) as exc:
  ```
  (`FileNotFoundError` is an `OSError`, which `READ_ERRORS` already covers.)
- `build/validate_database.py`: replace the local `READ_ERRORS = (OSError, EOFError, zlib.error, gzip.BadGzipFile)`
  with `from artifact import READ_ERRORS, VALID_CATEGORIES, decode, load_custom` and delete
  the now-unused `zlib` import.

Tests to add:

`tests/test_nodes.py` — the same middle-byte corruption `tests/test_validate.py` already
covers for the validator:

```python
def test_a_corrupt_database_returns_an_empty_string(node, tmp_path, monkeypatch):
    source = build_source(tmp_path / "corrupt")
    payload = bytearray(source.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    source.write_bytes(bytes(payload))
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    assert node.search("blue_h", "any", 32, True)[0] == ""
```

`tests/test_store.py`:

```python
def test_custom_payload_survives_a_corrupt_artifact(store, tmp_path, monkeypatch):
    source = build_source(tmp_path / "corrupt")
    payload = bytearray(source.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    source.write_bytes(bytes(payload))
    monkeypatch.setenv("DTA_LOCAL_ARTIFACT", str(source))
    (store.cache_dir() / "custom_tags.csv").write_text("a,0,1,\n", encoding="utf-8")

    assert store.custom_payload()["available"] is False
```

---

## I1. The real-artifact gate must not depend on the working directory

`tests/test_benchmark.py` resolves the real build relative to the process's working
directory, so a pytest run from anywhere but the repository root silently skips the gate —
the same "a gate that does not run" failure that C4 was raised about.

```python
REAL_ARTIFACT = Path(__file__).resolve().parents[1] / "generated" / "tags.bin.gz"
```

---

## I2. Test that the package loads the way ComfyUI loads it

`WEB_DIRECTORY`, the node mappings and the route registration are what ComfyUI reads from
`__init__.py`, and nothing in the committed suite executes that file — the plan's Step 6 probe
was manual. Turn it into a test.

Create `tests/test_package.py`:

```python
"""The package entry point must load the way ComfyUI loads it.

__init__.py is executed by ComfyUI's node loader as a package, with PromptServer already
constructed. The other tests import the node's modules directly and deliberately skip it.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

NODE_ROOT = Path(__file__).resolve().parents[1]


class RouteRecorder:
    def __init__(self):
        self.handlers = {}

    def get(self, path):
        def decorator(handler):
            self.handlers[path] = handler
            return handler

        return decorator


@pytest.fixture
def loaded_package(monkeypatch):
    recorder = RouteRecorder()
    server = types.ModuleType("server")
    server.PromptServer = types.SimpleNamespace(instance=types.SimpleNamespace(routes=recorder))
    monkeypatch.setitem(sys.modules, "server", server)
    monkeypatch.setitem(sys.modules, "folder_paths", types.ModuleType("folder_paths"))
    monkeypatch.delitem(sys.modules, "dta_package_probe", raising=False)

    spec = importlib.util.spec_from_file_location(
        "dta_package_probe", NODE_ROOT / "__init__.py", submodule_search_locations=[str(NODE_ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["dta_package_probe"] = module
    monkeypatch.setitem(sys.modules, "dta_package_probe", module)
    spec.loader.exec_module(module)
    return module, recorder


def test_entry_point_exposes_what_comfyui_reads(loaded_package):
    module, _ = loaded_package
    assert module.WEB_DIRECTORY == "./web"
    assert sorted(module.NODE_CLASS_MAPPINGS) == ["DanbooruTagSearch"]
    assert list(module.NODE_DISPLAY_NAME_MAPPINGS.values()) == ["Danbooru Tag Search"]


def test_entry_point_registers_the_routes(loaded_package):
    _, recorder = loaded_package
    assert sorted(recorder.handlers) == [
        "/danbooru-tag-autocomplete/custom",
        "/danbooru-tag-autocomplete/db",
        "/danbooru-tag-autocomplete/status",
    ]


def test_web_directory_exists(loaded_package):
    module, _ = loaded_package
    assert (NODE_ROOT / module.WEB_DIRECTORY / "search.js").is_file()
```

---

## I3. An overlay entry wins even when a filter excludes it

The spec's §8.2 says a custom entry replaces a main entry with the same canonical name. The
implementation filtered each source independently and then merged, so a main entry the
overlay had replaced came back whenever the filter admitted main and excluded the overlay —
`search("t", categories={0})` returned main's `t` even though the overlay claimed it.

The overlay is authoritative for every name it claims, including names it claims with an
entry the current filter would drop. Collect the claimed names without filters, drop them
from the main result, then merge the filtered overlay.

In `artifact.py`'s `TagIndex.search`, replace the custom branch with:

```python
        if self._custom is None:
            merged = self._search_source(self._main, key_bytes, limit, categories, exclude_deprecated)
        else:
            # The overlay is authoritative for every name it claims, including names it
            # claims with an entry the current filter would exclude, so the claimed names are
            # collected unfiltered and removed from the main result before the overlay merges.
            claimed = self._search_source(
                self._custom, key_bytes, self._custom.n_tags + self._custom.n_aliases, None, False
            )
            custom = self._search_source(
                self._custom, key_bytes, self._custom.n_tags + self._custom.n_aliases,
                categories, exclude_deprecated,
            )
            merged = self._search_source(
                self._main, key_bytes, limit + len(claimed), categories, exclude_deprecated
            )
            for name in claimed:
                merged.pop(name, None)
            merged.update(custom)
        return sorted(merged.values(), key=hit_sort_key)[:limit]
```

In `web/search.js`'s `search`, replace the custom branch with the same shape:

```javascript
    if (this.custom === null) {
      for (const hit of this.#searchSource(this.main, keyBytes, limit, categories, excludeDeprecated)) {
        merged.set(hit.name, hit);
      }
    } else {
      // Keep this in step with TagIndex.search in artifact.py: the overlay is authoritative
      // for every name it claims, even when the current filter would drop its entry.
      const claimed = this.#searchSource(
        this.custom, keyBytes, this.custom.nTags + this.custom.nAliases, null, false,
      );
      const custom = this.#searchSource(
        this.custom, keyBytes, this.custom.nTags + this.custom.nAliases, categories, excludeDeprecated,
      );
      for (const hit of this.#searchSource(this.main, keyBytes, limit + claimed.length, categories, excludeDeprecated)) {
        merged.set(hit.name, hit);
      }
      for (const hit of claimed) {
        merged.delete(hit.name);
      }
      for (const hit of custom) {
        merged.set(hit.name, hit);
      }
    }
```

Tests to add:

`tests/test_search.py`:

```python
def test_overlay_claims_a_name_even_when_the_filter_excludes_it():
    main = Artifact.from_tagset(TagSet(
        threshold=0,
        tags=(TagEntry("t0", 0, 100, False), TagEntry("t1", 0, 90, False)),
        aliases=(),
        alias_target=(),
    ))
    custom = Artifact.from_tagset(TagSet(
        threshold=0,
        tags=(TagEntry("t0", 4, 0, False),),
        aliases=(),
        alias_target=(),
    ))
    hits = TagIndex(main, custom=custom).search("t", limit=2, categories=frozenset({0}))
    assert [hit.name for hit in hits] == ["t1"]
```

The overlay's `t0` is category 4, so a category-0 filter excludes it; before this change the
main `t0` came back, and after it `t1` is the only remaining match.

`tests/test_search_js.mjs` — the fixture's overlay pair already has a category-4 `c0`
overriding a category-0 `c0`, so no new fixture file is needed:

```javascript
test("overlay precedence holds under a category filter", () => {
  const main = decodeArtifact(readBuffer(join(FIXTURES, "overlay-main.bin")));
  const custom = decodeArtifact(readBuffer(join(FIXTURES, "overlay.bin")));
  const index = new TagIndex(main, custom);
  assert.deepEqual(
    index.search("c", { limit: 2, categories: new Set([0]) }).map((hit) => hit.name),
    ["c1", "c2"],
  );
});
```

Also record the rule in the spec. In `docs/specs/2026-09-22-tag-autocomplete-design.md`
§8.2, append:

`custom 오버레이는 자기가 차지한 이름에 대해 항상 우선한다. 현재 필터가 오버레이 항목을 제외하더라도 그 이름을 main 결과에서 제거하며, 필터는 오버레이 병합 이후의 관점에 적용된다.`

---

## I4. Carried to M3 — the distributed one-character gate's margin

Measured locally: the distributed synthetic set (1,709,994 tags) reports a one-character p95
of 38.59 ms against a 50 ms budget — the tightest margin in the suite. The real artifact
reports 20.51 ms for the same query.

Not changed here because the budget is a controller decision and the right first step is
data, not a guess: M3's workflow must run the suite after building, and its first CI run
should record the gate's real number on a runner. If it lands near or above the budget, the
budget or the measurement shape is a controller ruling, not an edit by the implementer.

Until then the budget stands as written.
