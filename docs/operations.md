# Operations Runbook

All services are **systemd *user* units** (`systemctl --user …`). Paths use `~`
for the operator's home. Replace `<GPU-A-UUID>` / `<GPU-B-UUID>` with real values
from `nvidia-smi -L`.

---

## Services

| Unit | Role | Port |
|---|---|---|
| `container-qdrant.service` | Qdrant vector DB (podman) | 6333/6334 |
| `llama-embed.service` | Qwen3-Embedding-0.6B | 8082 |
| `llama-dolphin.service` | Dolphin-8B LLM | 8081 |
| `privategpt.service` | PrivateGPT orchestrator | 8001 |
| `llama-qwen.service` | *(disabled)* prior LLM, kept for rollback | 8081 |

```bash
# status / control
systemctl --user status  llama-dolphin
systemctl --user restart privategpt
systemctl --user stop    llama-embed

# health
curl -s 127.0.0.1:6333/healthz                       # qdrant -> 200
curl -s localhost:8081/v1/models                     # -> dolphin-8b
curl -s localhost:8082/v1/models                     # -> qwen3-embed
curl -s -o/dev/null -w '%{http_code}\n' localhost:8001/ui   # -> 307
```

Start order (dependencies are loose, but logically): `container-qdrant` →
`llama-embed` + `llama-dolphin` → `privategpt`.

---

## Ingest documents

```bash
# 1. drop sources, one subfolder per collection
cp *.pdf ~/pgpt/documents_raw/handbook/

# 2. PDF -> clean Markdown (marker, CPU venv)
~/marker-venv/bin/python ~/pgpt/preprocess.py            # --dry-run to preview

# 3. chunk + embed (dense+sparse) + store
python3 ~/pgpt/bulk-ingest.py                            # --dry-run / --force / --collection X
```
Both scripts are **idempotent** (sha256 manifests) — safe to re-run. One
**collection per top-level subfolder**.

> **First-ever hybrid ingest:** pre-warm the BM25 sparse model once, or the first
> request will stall downloading it:
> ```bash
> ~/.local/share/uv/tools/private-gpt/bin/python -c \
>   "from fastembed import SparseTextEmbedding as S; list(S('Qdrant/bm25').embed(['x']))"
> ```

---

## Query

- **UI:** `http://localhost:8001/ui`
- **API** (agentic RAG, scoped to a collection):
  ```bash
  curl -s localhost:8001/v1/messages -H 'Content-Type: application/json' -d '{
    "model":"dolphin-8b","max_tokens":300,
    "messages":[{"role":"user","content":"<question>"}],
    "tools":[{"name":"semantic_search","type":"semantic_search_v1",
              "context":[{"type":"ingested_artifact","context_filter":{"collection":"handbook"}}],
              "inputSchema":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}]
  }'
  ```
- **Retrieval only** (no generation): `POST /v1/tools/semantic-search`
  `{"query":"…","context_filter":{"collection":"handbook"}}`

---

## Evaluate quality (before bulk-ingesting a big corpus)

```bash
# ingest a representative sample, write an eval set (see evalset.example.jsonl), then:
python3 ~/pgpt/eval.py ~/pgpt/evalset.jsonl                 # retrieval + answer
python3 ~/pgpt/eval.py ~/pgpt/evalset.jsonl --retrieval-only
```
Reports retrieval hit-rate and answer accuracy so embedder/chunking/top_k changes
can be compared objectively.

---

## Common changes

| Goal | How |
|---|---|
| Dense-only (disable hybrid) | `PGPT_QDRANT_HYBRID_SEARCH=false` in `serve-pgpt.sh`, restart `privategpt` |
| Re-enable speculative decoding | set `DRAFT=~/models/Llama-3.2-1B-Instruct-Q8_0.gguf` in `serve-dolphin.sh`, restart |
| Tune chunk size | change embedder `CTX` in `serve-embed.sh` (chunk ≈ 0.9×CTX), restart `llama-embed`, **re-ingest** |
| Change `top_k` | `PGPT_RETRIEVAL_TOP_K` in `serve-pgpt.sh`, restart `privategpt` |
| Raise per-file size cap | `PGPT_MAXIMUM_BLOB_SIZE` (bytes) in `serve-pgpt.sh`, restart |

> **Re-ingest after changing the embedder or chunk size:** drop the collections
> (`curl -X DELETE 127.0.0.1:6333/collections/<name>`, or wipe `~/pgpt/qdrant_storage`)
> and run `bulk-ingest.py --force`.

---

## Rollback to the prior LLM (Qwen)

```bash
systemctl --user disable --now llama-dolphin llama-embed
systemctl --user enable  --now llama-qwen
```
(The Qwen service script/unit are left intact specifically for this.)

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Agentic RAG hallucinates, no tool call | LLM template lacks tools → `--chat-template-file` with a tool template (already set) |
| `'list' object has no attribute 'indices'` on ingest | embedded Qdrant IDF bug → use the **Qdrant server** (`PGPT_QDRANT_URL`) |
| `Sparse vector name cannot be empty` | stale collection from a config change → drop the collection / wipe `qdrant_storage`, re-ingest |
| `Only one of <location>,<url>,<path>…` | both `qdrant.url` and `qdrant.path` set → null `path` in `settings.yaml` |
| qdrant unreachable on `localhost:6333` | rootless podman is IPv4-only → use **`127.0.0.1`** |
| First hybrid ingest hangs | fastembed downloading BM25 → pre-warm it (above) |
| Embedding dim mismatch / bad retrieval | `PGPT_EMBED_DIM` must equal the embedder's dim (1024 here) |
| Wrong/garbage embeddings | Qwen3-Embedding needs `--pooling last` (not mean) |
| Big GGUF download hangs | `hf` CLI (huggingface_hub 1.8.0) bug → use `curl -C - --speed-time` |

---

## Install from scratch (outline)

1. Build/obtain a **CUDA-12.9 llama.cpp** (`llama-server`) for Pascal support.
2. Download GGUFs (via `curl`): Dolphin-2.9.4-llama3.1-8b Q8_0, Qwen3-Embedding-0.6B f16.
3. Extract the **Llama-3.1 tool template** (`/props` of any Llama-3.1 instruct
   model) to `~/models/llama31-tools.jinja`.
4. `uv tool install --python 3.11 --find-links https://wheels.privategpt.dev/packages/ "private-gpt[core]"`
   then `uv pip install --python <pgpt-venv> fastembed`.
5. `podman run … qdrant/qdrant` and wrap it as `container-qdrant.service`.
6. Create a CPU venv for `marker-pdf`.
7. Install the `serve-*.sh` wrappers + systemd units (see `scripts/`), set the env
   in `serve-pgpt.sh`, null `qdrant.path` in PrivateGPT's `settings.yaml`.
8. `systemctl --user enable --now container-qdrant llama-embed llama-dolphin privategpt`.
9. Pre-warm BM25, then verify with the commands above.

Proudly Made in Nebraska. Go Big Red! 🌽 https://xkcd.com/2347/
