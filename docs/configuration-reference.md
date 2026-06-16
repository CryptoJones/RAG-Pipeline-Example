# Configuration Reference

Every port, path, environment variable, CLI flag, and model parameter in the
pipeline, enumerated. (Host names, IPs, and machine-specific GPU UUIDs are
shown as placeholders.)

---

## 1. Network ports (all `localhost`)

| Port | Service | Protocol |
|---|---|---|
| `8081` | Dolphin-8B LLM (`llama-server`) | OpenAI-compatible HTTP |
| `8082` | Qwen3-Embedding embedder (`llama-server`) | OpenAI-compatible HTTP |
| `8001` | PrivateGPT | Anthropic-shaped HTTP + `/ui` |

---

## 2. Filesystem layout

```
~/models/
  dolphin-2.9.4-llama3.1-8b-Q8_0.gguf      # generation model (~8.0 GB)
  Qwen3-Embedding-0.6B-f16.gguf            # embedding model (~1.2 GB)
  Llama-3.2-1B-Instruct-Q8_0.gguf          # speculative draft (~1.3 GB, DISABLED)
  dolphin-2.9.1-llama-3-8b-Q8_0.gguf       # superseded (~8.0 GB, reclaimable)
  llama31-tools.jinja                      # Llama-3.1 tool chat template (override)
  serve-dolphin.sh                         # LLM launch wrapper
  serve-embed.sh                           # embedder launch wrapper
  serve-qwen.sh                            # prior LLM wrapper (rollback)

~/pgpt/
  serve-pgpt.sh                            # PrivateGPT launch wrapper
  preprocess.py                            # marker PDF→Markdown driver
  bulk-ingest.py                           # ingestion driver
  documents_raw/<collection>/              # raw source files you drop in
  documents/<collection>/                  # marker output (.md) + passthroughs
  local_data/                              # PrivateGPT SQLite state
  qdrant_storage/                          # Qdrant server data (podman volume)
  eval.py                                  # RAG eval harness
  evalset.example.jsonl                    # eval-set template
  .preprocess-manifest.tsv                 # sha256 manifest (preprocess)
  .ingest-manifest.tsv                     # sha256 manifest (ingest)

~/marker-venv/                             # isolated CPU venv for marker-pdf
~/.config/systemd/user/                    # systemd unit files
  container-qdrant.service                 # Qdrant server (podman, generated unit)
  llama-dolphin.service
  llama-embed.service
  privategpt.service
  llama-qwen.service                       # disabled (rollback)
~/.local/share/uv/tools/private-gpt/       # uv tool venv for PrivateGPT (+ fastembed)
```

---

## 3. systemd user units

All `Type=simple`, `Restart=on-failure`, `WantedBy=default.target`, `enabled`.

| Unit | ExecStart | State |
|---|---|---|
| `container-qdrant.service` | podman `qdrant/qdrant` (generated unit) | enabled, active |
| `llama-dolphin.service` | `~/models/serve-dolphin.sh` | enabled, active |
| `llama-embed.service` | `~/models/serve-embed.sh` | enabled, active |
| `privategpt.service` | `~/pgpt/serve-pgpt.sh` | enabled, active |
| `llama-qwen.service` | `~/models/serve-qwen.sh` | **disabled** (rollback) |

Manage with `systemctl --user {start,stop,restart,status} <unit>`.

---

## 4. Generation LLM — `serve-dolphin.sh` (port 8081)

### Environment knobs (with defaults)
| Var | Default | Meaning |
|---|---|---|
| `LLAMA_BIN` | `~/llama.cpp/build-multigpu/bin/llama-server` | the **CUDA-12.9** build (Pascal sm_61 support) |
| `MODEL` | `~/models/dolphin-2.9.4-llama3.1-8b-Q8_0.gguf` | generation model |
| `TEMPLATE` | `~/models/llama31-tools.jinja` | tool-calling chat template; `""` = use embedded |
| `DRAFT` | `""` (disabled) | speculative draft path; set to enable |
| `HOST` | `0.0.0.0` | bind address |
| `PORT` | `8081` | listen port |
| `CTX` | `16384` | context window |
| `PARALLEL` | `1` | concurrent sequences |
| `NGL` | `99` | GPU layers (all) |
| `KV_TYPE` | `q8_0` | KV cache quantization |
| `THREADS` | `6` | CPU threads |
| `ALIAS` | `dolphin-8b` | model id reported on the API |
| `CUDA_VISIBLE_DEVICES` | `<GPU-A-UUID>` | pins to GPU A by UUID |
| `LD_LIBRARY_PATH` | `/usr/local/cuda-12.9/lib64:…` | CUDA 12.9 runtime |

### Resulting `llama-server` flags
```
--model <MODEL> --alias dolphin-8b --host 0.0.0.0 --port 8081
--ctx-size 16384 --parallel 1 --n-gpu-layers 99
--cache-type-k q8_0 --cache-type-v q8_0 --threads 6
--flash-attn on --jinja --metrics
--chat-template-file ~/models/llama31-tools.jinja
# (if DRAFT set, also:) --device CUDA0 --model-draft <DRAFT>
#                       --device-draft CUDA1 --gpu-layers-draft 99 --spec-draft-n-max 16
```

---

## 5. Embeddings — `serve-embed.sh` (port 8082)

### Environment knobs (with defaults)
| Var | Default | Meaning |
|---|---|---|
| `LLAMA_BIN` | `~/llama.cpp/build-multigpu/bin/llama-server` | CUDA-12.9 build |
| `MODEL` | `~/models/Qwen3-Embedding-0.6B-f16.gguf` | embedding model |
| `HOST` | `0.0.0.0` | bind address |
| `PORT` | `8082` | listen port |
| `ALIAS` | `qwen3-embed` | model id reported on the API |
| `THREADS` | `4` | CPU threads |
| `CTX` | `2048` | context window — **also sets ingestion chunk size** (`≈0.9×CTX`) |
| `CUDA_VISIBLE_DEVICES` | `<GPU-B-UUID>` | pins to GPU B by UUID |
| `LD_LIBRARY_PATH` | `/usr/local/cuda-12.9/lib64:…` | CUDA 12.9 runtime |

### Resulting `llama-server` flags
```
--model <MODEL> --alias qwen3-embed --host 0.0.0.0 --port 8082
--embeddings --pooling last --embd-normalize 2
--ctx-size 2048 --n-gpu-layers 99 --threads 4
```
> **`--pooling last` is mandatory** for Qwen3-Embedding (last-token pooling). Mean
> pooling would silently produce wrong embeddings.

---

## 6. PrivateGPT — `serve-pgpt.sh` (port 8001)

Working directory is `~/pgpt` (so `local_data/` is created there). Launches
`private-gpt serve --host 0.0.0.0 --port 8001`.

### Environment variables set by the wrapper
| Var | Value | Meaning |
|---|---|---|
| `PORT` | `8001` | HTTP port (PrivateGPT reads `PORT`) |
| `PGPT_EMBED_DIM` | `1024` | **must match the embedder** (Qwen3-Embedding-0.6B = 1024) |
| `OPENAI_API_BASE` | `http://localhost:8081/v1` | LLM endpoint |
| `OPENAI_API_KEY` | `sk-local` | dummy (local server ignores it) |
| `OPENAI_EMBEDDING_API_BASE` | `http://localhost:8082/v1` | embedder endpoint |
| `OPENAI_EMBEDDING_API_KEY` | `sk-local` | dummy |
| `PGPT_LLM_DEFAULT` | `dolphin-8b` | default LLM (matches alias) |
| `PGPT_EMBEDDING_DEFAULT` | `qwen3-embed` | default embedder (matches alias) |
| `PGPT_QDRANT_HYBRID_SEARCH` | `true` | enable dense+sparse retrieval |
| `PGPT_RETRIEVAL_TOP_K` | `10` | chunks retrieved per query (lowered from default 32) |
| `PGPT_QDRANT_URL` | `http://127.0.0.1:6333` | use the Qdrant **server** (`127.0.0.1`, not `localhost`) |
| `PGPT_QDRANT_PATH` | `""` | unset embedded path so only `url` is used |

> Also: in PrivateGPT's `settings.yaml`, `qdrant.path` is set to **null** (the
> client rejects having both `url` and `path`), and **`fastembed`** is installed
> into the PrivateGPT venv (`uv pip install --python <pgpt-venv> fastembed`).

### Other relevant PrivateGPT settings (defaults, not overridden)
| Setting (env) | Default | Notes |
|---|---|---|
| `PGPT_MAXIMUM_BLOB_SIZE` | `26214400` (25 MiB) | per-file ingest cap (base64 path) |
| `PGPT_VECTORSTORE` | `qdrant` | vector DB |
| `PGPT_QDRANT_PATH` | (nulled) | embedded path disabled; the server `url` is used instead |
| `PGPT_QDRANT_DISTANCE_METRIC` | `cosine` | dense similarity metric |
| `PGPT_DEFAULT_COLLECTION` | `pgpt_collection` | default collection name |
| `PGPT_LLM_AUTO_DISCOVER_MODELS` | `true` | discover models from `/v1/models` |
| `PGPT_EMBEDDING_AUTO_DISCOVER_MODELS` | `true` | same for embeddings |
| sparse model | `Qdrant/bm25` | fastembed BM25 (must be pre-warmed; see ops) |
| chunking | sentence-tree, markdown-aware | `chunk_size ≈ 0.9 × embedder ctx (2048) ≈ 1843 tok` |

> `fastembed` is an extra dependency added to the PrivateGPT tool venv for hybrid
> search (`uv pip install --python <pgpt-venv> fastembed`).

---

## 7. `preprocess.py` (marker driver)

Run with the marker venv interpreter: `~/marker-venv/bin/python ~/pgpt/preprocess.py`

| Flag | Default | Meaning |
|---|---|---|
| `--raw` | `~/pgpt/documents_raw` | source tree |
| `--out` | `~/pgpt/documents` | Markdown output tree |
| `--manifest` | `~/pgpt/.preprocess-manifest.tsv` | sha256 skip-cache |
| `--collection` | (all) | restrict to one collection |
| `--device` | `cpu` (`TORCH_DEVICE`) | `cpu` or `cuda` |
| `--force` | off | reconvert everything |
| `--dry-run` | off | print plan, no work |

Handling by extension: **convert** (`.pdf .png .jpg .jpeg .tiff .tif .bmp .gif`
→ marker → `.md`), **copy** (`.md .txt`), **passthrough** (`.docx .xlsx .pptx
.csv`), else skip.

---

## 8. `bulk-ingest.py` (ingestion driver)

Run with system Python: `python3 ~/pgpt/bulk-ingest.py`

| Flag | Default | Meaning |
|---|---|---|
| `--base` | `http://localhost:8001` (`PGPT_BASE`) | PrivateGPT URL |
| `--docs` | `~/pgpt/documents` | tree to ingest |
| `--manifest` | `~/pgpt/.ingest-manifest.tsv` | sha256 skip-cache |
| `--collection` | (all) | restrict to one collection |
| `--force` | off | re-ingest everything |
| `--dry-run` | off | print plan, no API calls |

Behavior: one **collection per top-level subfolder** (loose files → `default`);
unique artifact id = path relative to the collection folder; skips unsupported
extensions and files > 25 MiB; **delete + re-ingest** on content change; honest
exit code (non-zero if any failures).

---

## 9. Models

| Role | Model | Quant | Dim / Ctx | Pooling | Notes |
|---|---|---|---|---|---|
| LLM | `dolphin-2.9.4-llama3.1-8b` (bartowski GGUF) | Q8_0 | ctx 16384 | — | Llama-3.1 base; tool template override |
| Embedder | `Qwen3-Embedding-0.6B` (Qwen GGUF) | f16 | 1024-dim | last | L2-normalized |
| Sparse | `Qdrant/bm25` (fastembed) | — | — | — | BM25; pre-warm before first ingest |
| Draft (disabled) | `Llama-3.2-1B-Instruct` (bartowski GGUF) | Q8_0 | — | — | speculative; no speedup, off |

---

## 10. GPU pinning & CUDA

- GPUs are pinned **by UUID** (not index) via `CUDA_VISIBLE_DEVICES`, so layout is
  stable across reboots/PCI reordering. Get UUIDs with `nvidia-smi -L`.
- When speculative decoding is enabled both GPUs are exposed and selected with
  `--device CUDA0` (target) / `--device-draft CUDA1` (draft).
- The **CUDA-12.9** llama.cpp build is required because newer CUDA dropped support
  for the older (Pascal) card; `LD_LIBRARY_PATH` points at `/usr/local/cuda-12.9/lib64`.

---

## 11. Quick verification commands

```bash
# models live?
curl -s localhost:8081/v1/models    # -> dolphin-8b
curl -s localhost:8082/v1/models    # -> qwen3-embed
curl -s -o /dev/null -w '%{http_code}\n' localhost:8001/ui   # -> 307

# embedder dimension (expect 1024) + normalization (expect ~1.0)
curl -s localhost:8082/v1/embeddings -d '{"model":"qwen3-embed","input":"x"}'

# LLM tool-calling works? (expect a tool_call)
curl -s localhost:8081/v1/chat/completions -d '{"model":"dolphin-8b","max_tokens":80,
  "messages":[{"role":"user","content":"weather in Paris?"}],
  "tools":[{"type":"function","function":{"name":"get_weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}],
  "tool_choice":"required"}'
```
