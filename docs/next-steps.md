# Next steps

What is left after M1 (data pipeline), M2a (search core and runtime), M2b (browser UI) and M3 (CI and
docs), in the order I would take it. Each item names the evidence it came from and how to tell it is
done. The milestone plans under `docs/plans/` hold the full context; this file is the entry point.

Current state: all four milestones are merged, `main` is green (161 pytest tests, 52 Node tests, and
the data workflow runs both on CI), the release `data-2026.09.23` is published, and the download path
has been verified end to end.

## 1. Walk the browser checklist

**Why first.** It is the only verification the browser layers and the live install path ever get, and
it has never been run. Everything below is secondary to a headline feature nobody has confirmed works
in a browser.

**What.** `README.md`'s "Browser checklist" section, in order, against a running ComfyUI. It covers
the widget hook in both renderers, the caret geometry, insertion and undo, the dropdown's mouse
handling, all eight settings, coexistence with pysssss's autocompleter, the error banner, custom tags,
and cache recovery.

**Done when.** Every line passes, or each failure becomes its own task. Two lines may need rewording
as you go: `Modern Node Design` has never been confirmed against the real UI, and the Nodes 2.0 items
depend on frontend behaviour nothing in this repository could test.

**Effort.** An hour with ComfyUI running.

## 2. Check for new data when the server starts

**Why.** The project's premise is a tag database that updates itself. Today the scheduled workflow
does its half — it publishes a new `data-<version>` release daily and commits the pointer — but a
running install never looks again. `ensure_download()` returns immediately when the cache is present,
and the status route only starts a download when the state is `missing`, so new data reaches a fresh
install or a user who deletes ComfyUI's user directory `danbooru-tag-autocomplete/` and reloads. That
manual step is what the checklist already tests, and `README.md` states it plainly.

**Shape.** On the first `/status` of a process, if the cache exists, fetch the pointer, compare its
`data_version` with the cached one, and start the existing background download when it is newer. Fail
silently when offline, which is what the design's §10.4 asks for. The download already replaces the
artifact atomically and invalidates the in-process caches by mtime and size, so the change is small;
the part to be careful with is the state machine's rule that a failed download is not retried
automatically, which must stay true.

**Watch out.** An already-open browser tab built its index when it loaded, so a mid-session update
needs a page reload to reach the dropdown. The design is also ambiguous here: §10.2 and §10.3 describe
downloading only when the cache is absent, while §10.4's "only the update check fails" implies a check
exists. This item resolves that in favour of updating.

**Done when.** Tests prove that a cached store with a newer pointer downloads, that an equal pointer
does nothing, that an unreachable pointer leaves the cache ready and usable, and that the checklist's
cache-recovery line still holds.

**Effort.** One small task, `store.py` plus tests.

## 3. Honour the design's two licence-driven build requirements

**Why.** §14 treats `HDiffusion/historical-danbooru-tag-counts` as a licensing risk — no LICENSE file,
only an `apache-2.0` card tag — and asks for two things the build does not do. The source repositories
have no environment override, so replacing one is a code edit; and there is no fallback, so if that
dataset is withdrawn the nightly build fails and data updates stop until someone edits
`SOURCE_ORDER` in `build/fetch_upstream.py`. The README's licence section states both limitations.

**Shape.** Make the sources' repository and revision targets overridable, and let an unavailable
source be skipped with a warning that lands in the release's `metadata.json`, so each artifact records
what it was actually built from.

**Done when.** A test builds with one source unavailable and asserts that the artifact is still
produced, that the warning is reported, and that `metadata.json` lists only the sources that
contributed.

**Effort.** One task, `build/` plus tests. Decide first whether a smaller artifact is acceptable,
because that is the trade §14 accepts.

## 4. Show `deprecated → canonical` again

**Why.** §8.3 asks the dropdown to show that relationship when the typed name is a deprecated alias
target, and `web/dropdown.js` has a `deprecated` badge for it. Nothing can reach that code:
`search()` defaults `excludeDeprecated: true`, and the alias search skips a deprecated target
outright, so no hit ever carries `deprecated: true`.

**Shape.** This is a search-semantics question, not a rendering one: decide whether a deprecated alias
target is suggested with a marker or skipped as it is now, then make the search and the renderer
agree. Both the Python and the browser implementations must change together, and
`tests/fixtures/queries.json` with `tests/test_search_js.mjs` exist to keep them in parity.

**Effort.** One task touching `artifact.py`, `web/search.js` and the fixtures.

## 5. Cut a code release, `v0.1.0`

**Why.** Data releases use the `data-*` prefix and are created with `--latest=false`, but with no code
release GitHub reports the newest published release as the repository's latest, so
`data-2026.09.23` currently holds that badge. The design separates the two kinds of release: data
releases are automatic, code releases are cut by hand.

**Shape.** Tag `v0.1.0` and write the notes from the four milestone plans. No code needs to change.

**Effort.** Minutes.

## Smaller items

None of these is worth a milestone. Each is recorded with its reasoning in the plan that found it.

| Item | Source | Note |
|---|---|---|
| `/db` sets no `ETag` or `Last-Modified` | design §10.3 | The design asks for both; the route sets `Cache-Control: no-cache` and nothing else, so every page load refetches 2.3 MB. |
| `/db` probes its file unguarded | M2b plan | A cache readable as a directory but not as a file can answer 500. M2b's Task 7 fixed only the directory case. |
| `formatPostCount(999_999)` renders `1000K` | M2b plan | Display only; the design pins 1000, 82000 and 1200000. |
| The caret mirror does not copy `direction` | M2b plan | An RTL prompt would report a wrong `left`. Danbooru tags are ASCII. |
| `web/keys.js` test gaps | M2b plan | No `count === 1`, no `page` larger than the list, no negative index with an unknown action. The code is correct on all three. |
| Each textarea keeps a dropdown and a mirror on `body` | M2b plan | Deleting a node leaves an empty hidden element. Both clear their contents when hidden, so no prompt text is retained. |
| Four exports have no importer | M2b plan | `SETTING_IDS`, `mirrorFor`, `CATEGORY_LABELS`, `CATEGORY_COLORS`. Kept as interface; a task's verification step asserts two of them. |
| The design's profile list is inaccurate | M3 plan | §5 names illustrious/noobai/pony/wai; only `danbooru` ships, because §13.1 gives no filter values for the others. |
| A failed `git push` in the workflow | M3 plan | The next run recovers, because change detection reads the repository's own pointer. Only the bot-only case has been observed. |

## How to tell the whole thing works

1. `.venv/bin/python -m pytest -q` and `node --test tests/*.mjs` are green.
2. `gh workflow run update-data.yml` succeeds, and its decide step reports either `publish=true` for
   new data or a missing release, or `publish=false` with the release present.
3. With no `DTA_*` overrides set, a fresh cache downloads the artifact the pointer names, its `sha256`
   matches, and a search returns real tags.
4. The browser checklist passes.
