# Design Decisions

Every significant choice in the pipeline, the rationale, and the dead-ends that
were ruled out along the way (so they aren't re-litigated later).

---

## RAG framework: PrivateGPT v2 (zylon-ai)
- Chosen as the RAG orchestrator. It **runs standalone** for `private-gpt serve`
  (embedded SQLite; no Postgres/Redis/RabbitMQ), exposes an Anthropic-shaped API
  (`/v1/messages`, `/v1/artifacts/ingest`, `/v1/tools/semantic-search`) plus a UI,
  and **auto-discovers** the LLM and embedder from their `/v1/models` endpoints.
- It drives RAG **agentically**: the LLM is given a `semantic_search_v1` tool and
  must *call* it to retrieve. This is elegant but puts a hard requirement on the
  LLM — it must support tool-calling (see below).
- Installed via `uv tool` to keep it isolated.

## LLM serving: local llama.cpp
- `llama-server` for both the LLM and the embedder — one engine, OpenAI-compatible,
  full control of quant/context/GPU placement, no external services.
- **The CUDA-12.9 build is required.** Newer CUDA dropped support for the older
  (Pascal) GPU; the 12.9 build keeps `sm_61` working. `LD_LIBRARY_PATH` points at
  `/usr/local/cuda-12.9/lib64`.

## Generation model: Dolphin-2.9.4-llama3.1-8b (Q8_0)
- Uncensored (Eric Hartford / cognitivecomputations), 8B, fits a 12 GB card.
- **Q8_0** quant: near-lossless vs f16 at half the VRAM/bandwidth (≈2× faster
  generation since decode is memory-bandwidth bound). 8B Q8 + 16K q8 KV ≈ 9 GB.
- **Dead-end → dolphin-2.9.1-llama-3-8b (Llama-3.0):** agentic RAG *hallucinated*
  because its GGUF chat template can't express tool calls. We swapped to **2.9.4**,
  which is **Llama-3.1**-based and has a real tool template available.

### Tool-calling: the override that makes agentic RAG work
- Symptom: PrivateGPT's `semantic_search` tool was never invoked → the LLM
  answered from imagination.
- Diagnosis: the bartowski Dolphin GGUF ships a **minimal ChatML template with no
  tools block** (291 chars), so llama.cpp has nothing to render/parse tool calls
  from — even `tool_choice:"required"` produced no call. We proved the *build* can
  tool-call (a stock Llama-3.2-1B returned a clean tool call), isolating the
  problem to the template.
- **Fix:** override with the Llama-3.1 instruct tool template via
  `--chat-template-file ~/models/llama31-tools.jinja`. Chat quality stays coherent
  (2.9.4 is Llama-3.1-based), and tool-calling now works → agentic RAG grounds.
- Caveat: the 8B is *chatty* — it sometimes issues several `semantic_search`
  calls (occasionally with a stray `artifacts` arg) before answering. Constraining
  the tool's `inputSchema` to just `query` helps. A larger model would be crisper.

## Embedding model: Qwen3-Embedding-0.6B (f16, 1024-dim)
- **Dead-end → nomic-embed-text-v1.5:** decent, but it *requires*
  `search_query:`/`search_document:` task prefixes that PrivateGPT's OpenAI
  embeddings path does **not** add → it runs below potential. Qwen3-Embedding is
  newer (2025), prefix-free, and stronger on retrieval benchmarks.
- **`--pooling last` is mandatory** (Qwen3-Embedding uses last-token pooling; mean
  pooling silently produces wrong vectors). `--embd-normalize 2` for cosine.
- f16 (not quantized): the model is tiny (0.6 B), so precision is cheap and
  embedding quality matters more than size.
- **Changing the embedder means re-ingesting** (vectors + `PGPT_EMBED_DIM`), so
  this was locked in *before* any real ingestion.

## GPU allocation: split by UUID
- LLM on **GPU A** (12 GB), embedder on **GPU B** (8 GB). Pinned with
  `CUDA_VISIBLE_DEVICES` set to **GPU UUIDs** (not indices) so the layout is stable
  across reboots / PCI reordering. Ingestion (embed-heavy) and query (gen-heavy)
  therefore never contend for VRAM.

## Retrieval: hybrid (dense + sparse BM25)
- Dense (Qwen3-Embedding) catches semantics; **sparse BM25** catches exact tokens
  — codes, IDs, acronyms, names — that dense embeddings blur. Both are stored per
  chunk and fused.
- **This required a real Qdrant server.** The journey:
  1. **Embedded Qdrant** (in-process) has an **IDF/BM25 bug**
     (`'list' object has no attribute 'indices'`) — hybrid is broken in local mode.
  2. Stood up a **Qdrant server** via `podman` (+ systemd user unit). PrivateGPT
     points at it with `PGPT_QDRANT_URL`.
  3. Added the **`fastembed`** package to the PrivateGPT venv (BM25 sparse vectors).
  4. Nulled `qdrant.path` in `settings.yaml` (the client rejects having both `url`
     and `path`).
  5. A transient `Sparse vector name cannot be empty` came from **stale collection
     state** left by the embedded→server migration — gone once collections are
     created fresh on the clean server. (Client 1.18.0 / server 1.18.2 are aligned,
     so it was **not** a version mismatch and needed **no source patch**.)
- Reach via **`127.0.0.1`**, not `localhost` — rootless podman publishes the port
  on IPv4 only, and `localhost` may resolve to IPv6 `::1`.
- To disable hybrid (dense-only fallback): `PGPT_QDRANT_HYBRID_SEARCH=false`.

## Chunking
- PrivateGPT v2 uses a **markdown-aware sentence-tree (auto-merging) parser**,
  with `chunk_size ≈ 0.9 × embedder_context_window`. There is **no fixed
  size/overlap knob**; the lever is the **embedder's `--ctx-size`** (2048 →
  ~1843-token ceiling, with smaller leaves merged at retrieval).

## `top_k` = 10 (down from default 32)
- With **no reranker** available, feeding 32 raw chunks to an 8B at 16K context
  dilutes the answer. Fewer, more-relevant chunks win.

## No reranker (known gap)
- PrivateGPT v2 has **no cross-encoder rerank stage** (confirmed: no `rerank` in
  the source). The classic "retrieve 30 → rerank → keep 6" isn't available via
  config. Mitigated with hybrid retrieval + a lower `top_k`. Revisit if PrivateGPT
  adds reranking or via a custom retrieval shim.

## Speculative decoding: tested, disabled
- Wired a Llama-3.2-1B draft on GPU B (vocab-compatible with Dolphin). Measured
  **40.0 tok/s (no draft) vs 40.1 tok/s (draft)** — the generic 1B draft diverges
  too much from the uncensored ChatML-Dolphin target (~0 % draft acceptance), so
  it adds overhead for no gain. **Off by default**, toggleable via `DRAFT=…`.

## PDF preprocessing: marker
- Convert PDFs → clean Markdown **before** ingestion. PDF text extraction is where
  most RAG quality is lost; marker preserves structure (headings/tables/reading
  order), which improves chunking and retrieval. (PrivateGPT's own heavy parser,
  docling, is configured as an external API that isn't running — another reason to
  pre-convert.)
- marker runs on **CPU in an isolated venv** so it never contends with the GPU
  services; preprocessing is an offline batch where reliability beats speed.

## Downloads: curl, not the `hf` CLI
- `huggingface_hub` 1.8.0 **hangs** (stuck in `futex_wait`) on large GGUF
  downloads. A `curl -C - --speed-limit/--speed-time` retry loop is reliable and
  held ~4 MB/s (no HF token available, so unauthenticated rate).

## Quant choices summary
| Model | Quant | Why |
|---|---|---|
| Dolphin-8B | Q8_0 | near-lossless, ½ VRAM/bandwidth vs f16, ~2× faster decode |
| Qwen3-Embedding-0.6B | f16 | tiny model; precision cheap, quality matters |
| Llama-3.2-1B (draft) | Q8_0 | small/fast draft (now disabled) |
