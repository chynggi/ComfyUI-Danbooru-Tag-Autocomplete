# ComfyUI Danbooru Tag Autocomplete

**English** | [한국어](README.md)

Danbooru tag autocomplete for ComfyUI, backed by Hugging Face tag metadata that updates itself
daily. Type in a prompt field and the matching tags appear under the caret; press `Tab` to insert
one.

- Design: `docs/specs/2026-09-22-tag-autocomplete-design.md`
- Plans: `docs/plans/`
- What is left: `docs/next-steps.md`

## Install

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/chynggi/ComfyUI-Danbooru-Tag-Autocomplete
```

Restart ComfyUI. On first run the extension downloads the tag database (about 2.3 MB) into
ComfyUI's user directory under `danbooru-tag-autocomplete/`, and reuses it offline afterwards.
There are no runtime dependencies to install and no build step. If the download is blocked, a
banner explains why and the prompt fields keep working without suggestions.

## Settings

In ComfyUI's settings, under the `DanbooruTagAutocomplete` group:

| Setting | Default | What it does |
|---|---|---|
| `Enable tag autocomplete` | on | turns the suggestions off entirely |
| `Suggestion count` | 32 | how many suggestions the list offers; about ten are visible at once |
| `Insert with Tab` | on | accept the highlighted tag with `Tab` |
| `Insert with Enter` | off | accept with `Enter` instead; off leaves `Enter` as a newline |
| `Insert spaces instead of underscores` | off | writes `blue hair` rather than `blue_hair` |
| `Show post counts` | on | shows each tag's post count |
| `Category to show` | `all` | restrict the list to a single category |
| `Enable alongside another autocomplete` | off | stay on when another autocomplete extension is installed |

## How the tag data updates

`data/latest.json` in this repository names the current release and its `sha256`. When the extension
has no cached database it reads that pointer, downloads `tags.bin.gz` from the release it names,
verifies the hash, and caches it under ComfyUI's user directory. A scheduled workflow rebuilds from
upstream every day at 03:00 KST, publishes a new `data-<version>` release only when the data changed,
and commits the matching `data/latest.json` back to the repository — so publishing new tag data never
needs a code update.

An install keeps the database it already has: it does not re-read the pointer on its own. To pick up
newer tags, delete ComfyUI's user directory `danbooru-tag-autocomplete/` and reload the page, and it
downloads the current release. A pointer published less than five minutes ago may still be served
cached by `raw.githubusercontent.com`, so if the data looks old, wait a moment and try again.

The sources are `hlibr/danbooru-tag-metadata-snapshot` for the tag set, categories and aliases, and
`HDiffusion/historical-danbooru-tag-counts` for daily post counts. Both are recorded with their
revisions in each release's `metadata.json`.

## Profiles

A profile is a YAML file under `profiles/` that decides what the artifact contains:

```yaml
name: danbooru
threshold: 25            # drop tags with fewer than this many posts
exclude_categories: []   # category numbers to drop: 0 general, 1 artist, 3 copyright, 4 character, 5 meta
exclude_deprecated: true # drop deprecated tags but keep their aliases
extra_sources: []        # source ids merged on top of the defaults
```

`danbooru` is the default. Illustrious, NoobAI, Pony and WAI prompts are written in the Danbooru tag
vocabulary, so one profile covers them; a model-specific profile would only repeat the defaults unless
it brought a tag source of its own. To build a narrower one — a higher threshold, or without
meta tags — copy `profiles/danbooru.yaml`, edit it, and build with `--profile profiles/<name>.yaml`.
`extra_sources` is the extension point for other boorus; a new source is a class under
`build/sources/` registered in `build/fetch_upstream.py`.

## Custom tags

Put a `custom_tags.csv` in ComfyUI's user directory under `danbooru-tag-autocomplete/`. A row whose
last column is empty declares a tag; a row whose last column holds names declares the row's name as
an alias of the first of them, so `my_old,general,0,my_tag` means typing `my_old` suggests `my_tag`.
A JSON file with the same shape is accepted instead. Custom entries win over the downloaded
database.

## Building the tag database yourself

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python pytest pyarrow pyyaml requests aiohttp
.venv/bin/python build/fetch_upstream.py --cache data/raw
.venv/bin/python build/build_database.py --profile profiles/danbooru.yaml --out generated
.venv/bin/python build/validate_database.py --artifact generated/tags.bin.gz --metadata generated/metadata.json
```

## Tests

```bash
.venv/bin/python -m pytest -q                  # everything, including the latency gates
.venv/bin/python -m pytest -m "not slow" -q    # skip the gates that build a 1.7M-tag set
.venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s
```

The gates measure the real artifact when `generated/tags.bin.gz` exists, plus two synthetic sets of
about 1.71 million tags that stress-test structural cost at roughly nine times the ~194,000 tags that
actually ship. That is why the update workflow builds before it tests.

## Development overrides

| Variable | Effect |
|---|---|
| `DTA_LOCAL_ARTIFACT` | use this `tags.bin.gz`, with its `metadata.json` beside it, instead of downloading |
| `DTA_LATEST_URL` | read the pointer from this URL instead of the repository |
| `DTA_REPO_SLUG` | override the repository whose committed pointer is read |

## Browser checklist

The browser behaviour is verified by hand, as the design specifies. Run ComfyUI, add a
`CLIPTextEncode` node, and walk these in order.

- Typing `1girl, blue_h` in the positive prompt shows a list starting `blue_hair`, then
  `blue_hairband`.
- The list shows a category badge, a post count, and `← alias` for alias matches.
- `↑`/`↓` move the highlight and wrap at the ends; `PageUp`/`PageDown` jump ten rows.
- `Tab` inserts the highlighted tag and `Escape` closes the list.
- With `Enter` insertion left off (the default), `Enter` adds a newline and the list closes,
  because the new line starts an empty token.
- Accepting `blue_hair` writes `1girl, blue_hair, ` and puts the caret after the new separator.
- Accepting inside `1girl, blue_h, solo` leaves the existing comma and spacing alone.
- With a long prompt that wraps, the list appears at the caret on the **first** line of the
  paragraph and on the **last** line, not at the field's top-left or at a fixed offset.
- After scrolling inside a tall prompt field so the caret is no longer on the visible first line,
  the list still appears beside the caret.
- After scrolling the page itself, the list still appears beside the caret.
- The negative prompt field, and any other node with a multiline string input, behaves the same.
- Two `CLIPTextEncode` nodes can be used one after the other with no cross-talk.
- Delete a node with a prompt field and add a new one; suggestions still appear in the new field.
- Typing a plain sentence with no matches leaves the field exactly as before: no interception,
  no swallowed keys, no flicker.
- With Nodes 2.0 (`Modern Node Design`) enabled, the same checks pass.
- Raising `Suggestion count` shows more rows and lowering it shows fewer.
- With `Insert spaces instead of underscores` on, accepting `blue_hair` writes `blue hair`.
- With `Show post counts` off, the rows carry no count.
- Setting `Category to show` to `character` leaves only character tags; `all` brings the rest back.
- With `Enable tag autocomplete` turned off in the settings, no list ever appears.
- With another autocomplete extension active, this one stays off and logs why; turning on
  `Enable alongside another autocomplete` makes it appear.
- Deleting `user/danbooru-tag-autocomplete/` and reloading the page starts a fresh download, and
  suggestions come back once it has finished.
- With the download blocked (offline), a dismissible banner explains why and typing still works.
- A `user/danbooru-tag-autocomplete/custom_tags.csv` with `my_tag,general,0,` and
  `my_old,general,0,my_tag` makes `my_old` suggest `my_tag`.

## Licence

MIT.

The tag data is built from two upstream datasets. Each release's `metadata.json` records every source
it used, with that source's revision and data date.

| Source | Licence | Note |
|---|---|---|
| `hlibr/danbooru-tag-metadata-snapshot` | MIT | generated from the Danbooru API |
| `HDiffusion/historical-danbooru-tag-counts` | **unclear** | no LICENSE file; its dataset card carries only an `apache-2.0` tag |

Treat the second as a risk. If it disappears or is withdrawn, drop it from `SOURCE_ORDER` in
`build/fetch_upstream.py` and the build runs on `hlibr` alone, with the artifact carrying one source
fewer. There is no environment override for the source repositories, so that is a code change rather
than a config change.

The raw datasets are never redistributed: a release carries only the built artifact, and the
downloaded raw files stay in a gitignored cache. The Danbooru tag vocabulary itself is treated as
factual information rather than copyrighted content, and no wiki text is used.
