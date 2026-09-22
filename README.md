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
