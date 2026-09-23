# ComfyUI Danbooru Tag Autocomplete

Danbooru tag autocomplete for ComfyUI, backed by continuously updated Hugging Face tag metadata.

Design: `docs/specs/2026-09-22-tag-autocomplete-design.md`
Plan (M1): `docs/plans/2026-09-22-m1-data-pipeline.md`

The GitHub Release asset referenced by `data/latest.json` is published by the M3 workflow, so its URL is not live until that workflow has run.

## Building the tag database

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python pytest pyarrow pyyaml requests
.venv/bin/python build/fetch_upstream.py --cache data/raw
.venv/bin/python build/build_database.py --profile profiles/danbooru.yaml --out generated
.venv/bin/python build/validate_database.py --artifact generated/tags.bin.gz --metadata generated/metadata.json
```

## Tests

```bash
.venv/bin/python -m pytest -m "not slow" -q
.venv/bin/python -m pytest tests/test_benchmark.py -m slow -v -s
```

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

## Custom tags

Put a `custom_tags.csv` in `user/danbooru-tag-autocomplete/`. A row whose last column is empty
declares a tag; a row whose last column holds names declares the row's name as an alias of the
first of them, so `my_old,general,0,my_tag` means typing `my_old` suggests `my_tag`. A JSON
file with the same shape is accepted instead. Custom entries win over the downloaded database.
