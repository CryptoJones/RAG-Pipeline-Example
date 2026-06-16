# Architecture

## 1. Topology

The pipeline is five cooperating processes on a single host, plus two on-disk
stores. Everything talks OpenAI/Anthropic-compatible HTTP over `localhost`.

```
                          ┌─────────────────────────── host ───────────────────────────┐
                          │                                                             │
  documents_raw/ ──▶ preprocess.py ──▶ documents/ ──▶ bulk-ingest.py                    │
   (PDF/DOCX/…)      (marker, CPU)      (.md)        (HTTP client)                       │
                          │                              │                              │
                          │                              ▼                              │
                          │                     PrivateGPT  (:8001)  ◀── User/Client    │
                          │                       │      │      ▲                       │
                          │            embeddings │      │ LLM  │ retrieve              │
                          │                       ▼      ▼      │                       │
                          │   llama.cpp :8082   llama.cpp :8081  │                       │
                          │   Qwen3-Embedding   Dolphin-8B       │                       │
                          │   [GPU B, 8 GB]     [GPU A, 12 GB]   │                       │
                          │                       │              │                       │
                          │                       ▼              │                       │
                          │           Qdrant server (podman) ◀───┘                       │
                          │              + SQLite (embedded)                             │
                          └─────────────────────────────────────────────────────────────┘
```

## 2. Processes

### 2.1 Generation LLM — `llama.cpp` `llama-server` (port 8081)
- Serves **Dolphin-2.9.4-llama3.1-8b** quantized **Q8_0** (~8.0 GB on disk).
- Pinned to **GPU A** (12 GB) by GPU UUID; all layers on-GPU (`-ngl 99`).
- Context window **16384**, KV cache quantized to **q8_0**, **flash-attention on**.
- **Tool-calling enabled** via an overridden Llama-3.1 chat template
  (`--chat-template-file`). This is what lets the agentic RAG work — see
  [design-decisions](design-decisions.md#tool-calling).
- Exposes OpenAI-compatible `/v1/chat/completions`, `/v1/models`, `/props`,
  `/metrics`. Model alias: **`dolphin-8b`**.

### 2.2 Embeddings — `llama.cpp` `llama-server` (port 8082)
- Serves **Qwen3-Embedding-0.6B** in **f16** (~1.2 GB), **1024-dim** output.
- Pinned to **GPU B** (8 GB) by GPU UUID.
- **`--pooling last`** (Qwen3-Embedding uses last-token pooling, *not* mean) and
  **`--embd-normalize 2`** (L2-normalized vectors for cosine similarity).
- Context window **2048** — *this value also sets the ingestion chunk size*
  (PrivateGPT computes `chunk_size ≈ 0.9 × embedder_context_window`).
- Embedding-only server (`--embeddings`). Model alias: **`qwen3-embed`**.

### 2.3 RAG orchestrator — PrivateGPT (port 8001)
- `zylon-ai/private-gpt`, installed as a `uv` tool (Python 3.11).
- **Runs standalone** — embedded SQLite + a Qdrant **server** (podman). No
  Postgres, Redis, or RabbitMQ required for `private-gpt serve`.
- `fastembed` is added to its venv to produce BM25 sparse vectors for hybrid.
- Auto-discovers the LLM and embedder from their `/v1/models` endpoints.
- Anthropic-shaped API: `/v1/messages`, `/v1/artifacts/ingest`,
  `/v1/tools/semantic-search`, `/v1/models`, plus a web UI at `/ui`.
- Drives RAG **agentically**: it gives the LLM a `semantic_search_v1` tool and
  the LLM calls it to retrieve.

### 2.4 Vector store — Qdrant **server** (podman) + fastembed BM25
- A real Qdrant server runs as a `podman` container (systemd user unit
  `container-qdrant.service`) at `127.0.0.1:6333`, storage under
  `~/pgpt/qdrant_storage`. PrivateGPT connects via `PGPT_QDRANT_URL`.
- A server (not embedded mode) is **required for hybrid** — the in-process
  embedded Qdrant client has an IDF/BM25 bug. See
  [design-decisions](design-decisions.md#retrieval-hybrid-dense--sparse-bm25).
- **Hybrid retrieval**: a dense vector (1024-dim, from Qwen3-Embedding) **and** a
  sparse vector (BM25, from `fastembed` model `Qdrant/bm25`) per chunk.
- Dense catches semantic similarity; sparse catches exact tokens (codes, IDs,
  acronyms, names). Results are fused.

### 2.5 Doc/index store — SQLite (embedded)
- PrivateGPT's metadata, artifact registry, and index bookkeeping. Migrations run
  automatically on first start. Lives under `~/pgpt/local_data`.

### 2.6 Preprocessing — `marker` (`marker-pdf`, isolated venv)
- Converts PDFs (and DOCX/PPTX/images) to clean, structured Markdown using Surya
  layout/OCR models. Runs on **CPU** in a dedicated venv (`~/marker-venv`) so it
  never contends with the GPU services.
- Driven by `preprocess.py`, which loads marker once and walks `documents_raw/`.

## 3. GPU / VRAM allocation

| GPU | VRAM | Tenant | ~Usage |
|---|---|---|---|
| **GPU A** | 12 GB | Dolphin-8B Q8_0 + 16K KV (q8_0) | ~9.0 GB |
| **GPU B** | 8 GB | Qwen3-Embedding-0.6B f16 | ~1.8 GB |

The split is deliberate: the LLM and embedder live on different cards, so
ingestion (embedding-heavy) and querying (generation-heavy) never fight for VRAM.
GPU B has ample headroom (it previously also hosted an optional speculative-decode
draft — now disabled).

## 4. Request lifecycle

### 4.1 Ingestion
1. `preprocess.py` reads `documents_raw/<collection>/<file>`, converts PDFs/images
   to Markdown via marker, writes `documents/<collection>/<file>.md`. (Text/MD are
   copied through; DOCX/XLSX/PPTX are passed through for PrivateGPT's own parsers.)
   A sha256 manifest skips unchanged sources.
2. `bulk-ingest.py` walks `documents/`, base64-encodes each file, and
   `POST`s `/v1/artifacts/ingest` with `{artifact, collection, input, metadata}`.
   One **collection per top-level subfolder**. Its own sha256 manifest makes it
   idempotent (skips unchanged, delete+re-ingests changed).
3. PrivateGPT parses → chunks (markdown-aware sentence-tree, sized to the embedder
   context window) → embeds each chunk **dense** (Qwen3-Embedding via :8082) and
   **sparse** (fastembed BM25 in-process) → upserts both into Qdrant, metadata
   into SQLite.

### 4.2 Query (agentic)
1. Client `POST`s `/v1/messages` with the question and a `semantic_search` tool
   scoped (via `context`) to a collection.
2. PrivateGPT prompts **Dolphin-8B** (`:8081`) with the question + tool spec.
3. The LLM emits a **`semantic_search` tool call** (works because of the Llama-3.1
   tool template).
4. PrivateGPT runs a **hybrid retrieval** (dense + sparse, `top_k=10`) against
   Qdrant for that collection.
5. The retrieved chunks are returned to the LLM as the tool result.
6. The LLM produces a **grounded answer** (with citations), returned to the client.

## 5. Startup / supervision

All three services are **systemd user units** (`--user`), `enabled` so they
start on boot. Each is a thin wrapper script `exec`-ing the real binary, so config
lives in the script (and is overridable by env). See
[operations.md](operations.md) and [configuration-reference.md](configuration-reference.md).
