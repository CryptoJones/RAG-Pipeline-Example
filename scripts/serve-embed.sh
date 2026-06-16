#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Serve Qwen3-Embedding-0.6B as an OpenAI-compatible /v1/embeddings endpoint on
# the GTX 1080. Qwen3-Embedding uses LAST-token pooling (NOT mean) and emits
# 1024-dim vectors. MUST use the CUDA-12.9 build (Pascal sm_61 support).
#
# NOTE: PrivateGPT derives its ingestion chunk size from this server's context
# window (chunk_size ~= 0.9 * CTX), so CTX here also tunes chunk granularity.
set -euo pipefail

LLAMA_BIN="${LLAMA_BIN:-/home/USER/llama.cpp/build-multigpu/bin/llama-server}"
MODEL="${MODEL:-/home/USER/models/Qwen3-Embedding-0.6B-f16.gguf}"
HOST="${HOST:-0.0.0.0}"; PORT="${PORT:-8082}"
ALIAS="${ALIAS:-qwen3-embed}"; THREADS="${THREADS:-4}"
CTX="${CTX:-2048}"   # also sets PrivateGPT chunk ceiling (~0.9*CTX tokens)

export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-<GPU-B-UUID-gtx1080>}"
export LD_LIBRARY_PATH="/usr/local/cuda-12.9/lib64:${LD_LIBRARY_PATH:-}"

exec "$LLAMA_BIN" \
  --model "$MODEL" --alias "$ALIAS" \
  --host "$HOST" --port "$PORT" \
  --embeddings --pooling last --embd-normalize 2 \
  --ctx-size "$CTX" --n-gpu-layers 99 --threads "$THREADS"
