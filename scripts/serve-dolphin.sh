#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Serve Dolphin-2.9.4-Llama-3.1-8B (uncensored, Eric Hartford) as an
# OpenAI-compatible endpoint. Target (8B) on the RTX 3060; optional speculative
# decoding draft (Llama-3.2-1B, vocab-compatible) on the GTX 1080 for faster gen.
# TEMPLATE override gives tool-calling (PrivateGPT agentic RAG needs it).
# Set DRAFT="" to disable speculative decoding; TEMPLATE="" for embedded template.
set -euo pipefail

LLAMA_BIN="${LLAMA_BIN:-/home/USER/llama.cpp/build-multigpu/bin/llama-server}"
MODEL="${MODEL:-/home/USER/models/dolphin-2.9.4-llama3.1-8b-Q8_0.gguf}"
TEMPLATE="${TEMPLATE:-/home/USER/models/llama31-tools.jinja}"
DRAFT="${DRAFT:-}"
HOST="${HOST:-0.0.0.0}"; PORT="${PORT:-8081}"; CTX="${CTX:-16384}"
PARALLEL="${PARALLEL:-1}"; NGL="${NGL:-99}"; KV_TYPE="${KV_TYPE:-q8_0}"; THREADS="${THREADS:-6}"
ALIAS="${ALIAS:-dolphin-8b}"
GPU_3060="<GPU-A-UUID-rtx3060>"
GPU_1080="<GPU-B-UUID-gtx1080>"
export LD_LIBRARY_PATH="/usr/local/cuda-12.9/lib64:${LD_LIBRARY_PATH:-}"

ARGS=(
  --model "$MODEL" --alias "$ALIAS"
  --host "$HOST" --port "$PORT"
  --ctx-size "$CTX" --parallel "$PARALLEL"
  --cache-type-k "$KV_TYPE" --cache-type-v "$KV_TYPE"
  --threads "$THREADS" --flash-attn on --jinja --metrics
)
[ -n "$TEMPLATE" ] && ARGS+=(--chat-template-file "$TEMPLATE")

if [ -n "$DRAFT" ]; then
  # Speculative decoding: 8B target on 3060 (CUDA0), 1B draft on 1080 (CUDA1).
  export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-$GPU_3060,$GPU_1080}"
  ARGS+=(--device CUDA0 --n-gpu-layers "$NGL")
  ARGS+=(--model-draft "$DRAFT" --device-draft CUDA1 --gpu-layers-draft 99 --spec-draft-n-max 16)
else
  export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-$GPU_3060}"
  ARGS+=(--n-gpu-layers "$NGL")
fi

exec "$LLAMA_BIN" "${ARGS[@]}"
