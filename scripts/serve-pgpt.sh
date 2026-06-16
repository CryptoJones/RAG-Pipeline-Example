#!/usr/bin/env bash
# PrivateGPT (zylon-ai) RAG server — middleware only. LLM = :8081 (Dolphin), the
# embedder = :8082 (Qwen3-Embedding-0.6B, 1024-dim). Persistence is embedded
# sqlite + on-disk Qdrant under ./local_data (CWD = /home/USER/pgpt).
set -euo pipefail
export PATH="/home/USER/.local/bin:$PATH"
cd /home/USER/pgpt

export PORT="${PORT:-8001}"
export PGPT_EMBED_DIM="${PGPT_EMBED_DIM:-1024}"                # Qwen3-Embedding-0.6B = 1024 dims
export OPENAI_API_BASE="${OPENAI_API_BASE:-http://localhost:8081/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-sk-local}"
export OPENAI_EMBEDDING_API_BASE="${OPENAI_EMBEDDING_API_BASE:-http://localhost:8082/v1}"
export OPENAI_EMBEDDING_API_KEY="${OPENAI_EMBEDDING_API_KEY:-sk-local}"
export PGPT_LLM_DEFAULT="${PGPT_LLM_DEFAULT:-dolphin-8b}"
export PGPT_EMBEDDING_DEFAULT="${PGPT_EMBEDDING_DEFAULT:-qwen3-embed}"
export PGPT_QDRANT_HYBRID_SEARCH="${PGPT_QDRANT_HYBRID_SEARCH:-true}"   # HYBRID dense + sparse(BM25) via Qdrant server (needs fresh collections; embedded mode has an IDF bug)
export PGPT_RETRIEVAL_TOP_K="${PGPT_RETRIEVAL_TOP_K:-10}"               # lowered from 32 (no reranker)

export PGPT_QDRANT_URL="${PGPT_QDRANT_URL:-http://127.0.0.1:6333}"
export PGPT_QDRANT_PATH=""
export PGPT_QDRANT_PREFER_GRPC="false"
exec private-gpt serve --host 0.0.0.0 --port "$PORT"
