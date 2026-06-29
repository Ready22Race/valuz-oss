# Vendored tiktoken vocabulary

This directory holds the [tiktoken](https://github.com/openai/tiktoken) BPE
vocabulary used by the **goal-mode length fence**
(`backend/valuz_agent/adapters/agent_resolver.py` → `estimate_tokens`). It lets
the packaged, **offline** app count a task/subtask goal in tokens — to decide
when to spill an over-long goal to a doc — without ever reaching the network.

## What's here

- `fb374d419588a4632f3f557e76b4b70aebbca790` — the `o200k_base` vocab blob.

tiktoken caches a vocab under `$TIKTOKEN_CACHE_DIR/<sha1(blob_url)>` and reads it
(after a sha256 check) **before** downloading. The filename above is exactly
`sha1("https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken")`,
so pointing `TIKTOKEN_CACHE_DIR` here is a guaranteed cache hit.

## How it's wired (the closed loop)

1. **Runtime** — `_vendored_tiktoken_cache_dir()` resolves this dir
   (`VALUZ_TIKTOKEN_CACHE_DIR` override → frozen `_MEIPASS/vendor/tiktoken` →
   dev `backend/vendor/tiktoken`) and sets `TIKTOKEN_CACHE_DIR` before the first
   `get_encoding`. If nothing is found, counting falls back to a char heuristic.
2. **Packaging** — `backend/scripts/valuz_agent.spec` bundles this dir as the
   `vendor/tiktoken` data dir and lists `tiktoken` + `tiktoken_ext` (the encoding
   constructors) as hidden imports, so `get_encoding` resolves in the frozen build.

## Refresh

Platform-independent — one committed copy serves every platform. To regenerate
(e.g. when bumping the encoding):

```bash
bash scripts/download-tiktoken.sh
```

The script pins the blob URL + expected sha256 (mirrored from
`tiktoken_ext/openai_public.py`); bump both there if the encoding changes.
