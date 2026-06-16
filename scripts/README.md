# scripts/

Genericized copies of the actual pipeline scripts. **Replace the placeholders
before use:**

- `/home/USER` → your home directory
- `<GPU-A-UUID-rtx3060>` / `<GPU-B-UUID-gtx1080>` → real UUIDs from `nvidia-smi -L`
- Paths assume a CUDA-12.9 `llama-server` at `~/llama.cpp/build-multigpu/bin/`

| File | Purpose |
|---|---|
| `serve-dolphin.sh` | launch the LLM (`llama-server`, port 8081) — incl. tool template + (disabled) speculative draft |
| `serve-embed.sh` | launch the embedder (`llama-server`, port 8082) — Qwen3-Embedding, last-pooling |
| `serve-pgpt.sh` | launch PrivateGPT (port 8001) — all the `PGPT_*` / `OPENAI_*` env |
| `preprocess.py` | marker PDF→Markdown (run with the marker venv's python) |
| `bulk-ingest.py` | idempotent ingestion driver (system python) |
| `eval.py` + `evalset.example.jsonl` | RAG quality eval harness |
| `systemd/*.service` | systemd **user** units for the three `llama`/`pgpt` services |

## Steps not captured in these files

```bash
# 1. PrivateGPT + fastembed (hybrid sparse vectors)
uv tool install --python 3.11 --find-links https://wheels.privategpt.dev/packages/ "private-gpt[core]"
uv pip install --python ~/.local/share/uv/tools/private-gpt/bin/python fastembed

# 2. null qdrant.path in PrivateGPT's settings.yaml (so PGPT_QDRANT_URL is used)
#    .../site-packages/settings.yaml :  path: ${PGPT_QDRANT_PATH:}

# 3. Qdrant server (podman) + systemd user unit
podman run -d --name qdrant --restart=always -p 6333:6333 -p 6334:6334 \
  -v ~/pgpt/qdrant_storage:/qdrant/storage:Z docker.io/qdrant/qdrant:latest
podman generate systemd --new --name qdrant > ~/.config/systemd/user/container-qdrant.service
podman rm -f qdrant && systemctl --user daemon-reload && systemctl --user enable --now container-qdrant

# 4. pre-warm the BM25 sparse model (first hybrid ingest otherwise stalls)
~/.local/share/uv/tools/private-gpt/bin/python -c \
  "from fastembed import SparseTextEmbedding as S; list(S('Qdrant/bm25').embed(['x']))"

# 5. enable everything
systemctl --user enable --now container-qdrant llama-embed llama-dolphin privategpt
```

See [`../docs/operations.md`](../docs/operations.md) for the full runbook and
[`../docs/configuration-reference.md`](../docs/configuration-reference.md) for
every flag/env.

Proudly Made in Nebraska. Go Big Red! 🌽 https://xkcd.com/2347/
