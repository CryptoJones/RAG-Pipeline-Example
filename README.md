# RAG-Pipeline-Example

> A fully self-hosted, GPU-split **Retrieval-Augmented Generation** pipeline —
> local LLM, local embeddings, hybrid vector search, and PDF→Markdown
> preprocessing — wired together and documented end to end.

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Deployment](https://img.shields.io/badge/deployment-self--hosted%20%C2%B7%20local-success)
![RAG](https://img.shields.io/badge/RAG-PrivateGPT%20v2-d2a8ff)
![LLM](https://img.shields.io/badge/LLM-Dolphin--8B%20%C2%B7%20llama.cpp-a5d6ff)
![Embeddings](https://img.shields.io/badge/embeddings-Qwen3--Embedding--0.6B-a5d6ff)
![Vector DB](https://img.shields.io/badge/vector%20DB-Qdrant%20%C2%B7%20hybrid-2ecc71)
![Preprocessing](https://img.shields.io/badge/PDF%E2%86%92Markdown-marker-ffb454)

This repository is the **design documentation** for that pipeline: an annotated
architecture, a colour-coded pipeline graph, an exhaustively enumerated
configuration reference, the design decisions (including the trade-offs and
dead-ends), an operations runbook, and the (genericized) scripts.

> Everything runs **on-premises** — no data leaves the host, no per-token API
> costs. Host names, IPs, and hardware identifiers are intentionally omitted;
> GPUs are referred to as **GPU A** (12 GB) and **GPU B** (8 GB).

---

## Contents

- [Pipeline at a glance](#pipeline-at-a-glance)
- [Highlights](#highlights)
- [Verified working](#verified-working)
- [Components](#components)
- [The two data paths](#the-two-data-paths)
- [Quickstart](#quickstart)
- [Documentation](#documentation)
- [Known limitations](#known-limitations)
- [License](#license)

---

## Pipeline at a glance

![RAG pipeline diagram](assets/pipeline.png)

<sub>Source: [`docs/pipeline.dot`](docs/pipeline.dot) · scalable: [`assets/pipeline.svg`](assets/pipeline.svg)</sub>

```mermaid
flowchart LR
  subgraph ING["1 - Ingestion (offline)"]
    raw[("documents_raw/<br/>PDF · DOCX · images")] --> pre["preprocess.py<br/>marker to Markdown"]
    pre --> md[("documents/<br/>clean .md")] --> ing["bulk-ingest.py<br/>idempotent"]
  end
  subgraph HW["GPUs"]
    emb["Qwen3-Embedding-0.6B<br/>GPU B · 1024-d · last-pool"]
    llm["Dolphin-2.9.4-8B<br/>GPU A · Q8_0 · tool-calling"]
  end
  qd[("Qdrant server<br/>hybrid: dense + sparse BM25")]
  pg["PrivateGPT<br/>agentic RAG · web UI"]
  usr(["User / Client"])

  ing --> emb --> qd
  usr --> pg --> llm
  llm -. "semantic_search()" .-> pg
  pg --> qd --> pg
  pg --> usr

  classDef script fill:#ffb454,stroke:#d98b1f,color:#1a1a1a;
  classDef model fill:#a5d6ff,stroke:#1f6feb,color:#0d1117;
  classDef store fill:#7ee787,stroke:#2ea043,color:#0d1117;
  classDef svc fill:#d2a8ff,stroke:#8957e5,color:#0d1117;
  class pre,ing script;
  class emb,llm model;
  class qd,raw,md store;
  class pg svc;
```

---

## Highlights

- **Dual-GPU split** — the generation model and the embedding model live on
  separate cards, so ingestion (embedding-heavy) and querying (generation-heavy)
  never contend for VRAM.
- **Hybrid retrieval** — dense vectors (semantic) **plus** sparse BM25 (exact
  tokens: codes, IDs, names, acronyms), fused, on a real Qdrant server.
- **Agentic RAG that actually grounds** — the LLM *calls* a retrieval tool; a
  chat-template override gives an uncensored ChatML-trained model reliable
  tool-calling that it otherwise lacks.
- **Quality-first preprocessing** — PDFs are converted to clean, structured
  Markdown with `marker` before chunking, which beats raw-PDF text extraction.
- **Fully local & API-compatible** — OpenAI/Anthropic-shaped HTTP throughout;
  PrivateGPT runs standalone (embedded SQLite); no Postgres/Redis/RabbitMQ.
- **Operable** — idempotent ingestion (sha256 manifests), systemd-managed
  services, a one-command rollback path, and a built-in **evaluation harness**
  to tune retrieval objectively before committing a large corpus.

---

## Verified working

Each stage was tested end to end:

- ✅ **Ingest → retrieve → grounded answer** through the agentic API, with
  citations (the LLM correctly answers *only* from ingested content).
- ✅ **Hybrid retrieval** — an exact-code query is found via the BM25/sparse side
  **and** a paraphrased query is found via the dense side.
- ✅ **Embeddings** — 1024-dim, L2-normalized, last-token pooled (Qwen3-Embedding).
- ✅ **Tool-calling** — the LLM emits well-formed tool calls (via the template fix).
- ✅ **Idempotent ingestion** — re-running skips unchanged files; changed files
  are re-ingested.
- ✅ **PDF→Markdown** — `marker` produces structured Markdown from source PDFs.
- ✅ **Eval harness** — reports retrieval hit-rate and answer accuracy.

A query is a single API call; the orchestrator handles retrieval + grounding:

```bash
curl -s localhost:8001/v1/messages -H 'Content-Type: application/json' -d '{
  "model":"dolphin-8b","max_tokens":300,
  "messages":[{"role":"user","content":"<your question>"}],
  "tools":[{"name":"semantic_search","type":"semantic_search_v1",
            "context":[{"type":"ingested_artifact",
                        "context_filter":{"collection":"handbook"}}],
            "inputSchema":{"type":"object",
                           "properties":{"query":{"type":"string"}},
                           "required":["query"]}}]
}'
# -> a grounded answer drawn from the "handbook" collection, with sources.
```

---

## Components

| Layer | Software | Model | Where | Port |
|---|---|---|---|---|
| Generation LLM | llama.cpp `llama-server` | **Dolphin-2.9.4-llama3.1-8b** Q8_0 | GPU A (12 GB) | `8081` |
| Embeddings | llama.cpp `llama-server` | **Qwen3-Embedding-0.6B** f16 (1024-dim) | GPU B (8 GB) | `8082` |
| RAG orchestrator | PrivateGPT (`private-gpt`, `uv` tool) | — (middleware) | CPU/RAM | `8001` |
| Vector store | Qdrant **server** (podman) + fastembed BM25 | — | disk/RAM | `6333` |
| Doc/index store | SQLite (embedded) | — | disk | — |
| PDF preprocessing | marker (`marker-pdf`, dedicated venv) | Surya models | CPU | — |

---

## The two data paths

**1 — Ingestion (offline).** Drop files in `documents_raw/<collection>/` →
`preprocess.py` converts PDFs to clean Markdown with `marker` → `bulk-ingest.py`
sends each file to PrivateGPT, which chunks it, embeds the chunks **dense**
(Qwen3-Embedding) **and** **sparse** (fastembed BM25), and stores both in Qdrant.
One collection per top-level subfolder.

**2 — Query (online, agentic).** A question goes to PrivateGPT → it prompts the
Dolphin LLM with a tool spec → the LLM **calls `semantic_search`** → PrivateGPT
runs a **hybrid** retrieval against Qdrant → the top-k chunks are returned to the
LLM → it produces a **grounded answer with citations**.

---

## Quickstart

Full runbook: **[docs/operations.md](docs/operations.md)**. The short version:

```bash
# ingest
cp mydocs/*.pdf  ~/pgpt/documents_raw/handbook/
~/marker-venv/bin/python ~/pgpt/preprocess.py     # PDF → Markdown
python3 ~/pgpt/bulk-ingest.py                      # chunk + embed + store

# ask
xdg-open http://localhost:8001/ui                  # or POST /v1/messages (above)

# measure quality before scaling up
python3 ~/pgpt/eval.py ~/pgpt/evalset.jsonl
```

---

## Documentation

| Doc | What's in it |
|---|---|
| **[docs/architecture.md](docs/architecture.md)** | Components, data flow, GPU/VRAM allocation, request lifecycle |
| **[docs/configuration-reference.md](docs/configuration-reference.md)** | **Every** env var, CLI flag, port, path, and model parameter |
| **[docs/design-decisions.md](docs/design-decisions.md)** | Why each choice was made — the trade-offs and the dead-ends |
| **[docs/operations.md](docs/operations.md)** | Install, start/stop, rollback, ingestion workflow, troubleshooting |
| **[scripts/](scripts/)** | Genericized serve wrappers, preprocess/ingest/eval drivers, systemd units |

---

## Known limitations

- **No reranker** — PrivateGPT v2 has no cross-encoder rerank stage; precision is
  managed with hybrid retrieval and a tuned `top_k`. ([details](docs/design-decisions.md#no-reranker-known-gap))
- **Small-model tool-calling is chatty** — the 8B occasionally issues several
  retrieval calls before answering; correct, but a larger model is crisper.
- **Hybrid requires a Qdrant server** — the embedded Qdrant client can't do BM25
  hybrid; a containerized server is used instead. ([details](docs/design-decisions.md#retrieval-hybrid-dense--sparse-bm25))

---

## License

MIT — see [LICENSE](LICENSE). © 2026 CryptoJones.
