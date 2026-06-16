#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""eval.py - measure RAG quality against a small Q&A eval set.

Use this BEFORE bulk-ingesting a large corpus: ingest a representative sample,
write an eval set, and run this to compare embedders / chunking / hybrid / top_k
objectively instead of by vibes.

Eval set = JSONL, one item per line:
  {"question": "...",
   "collection": "handbook",
   "expect": "substring that must appear in the ANSWER",        # optional
   "expect_retrieved": "substring that must appear in a CHUNK"}  # optional

Run:
  python3 eval.py evalset.jsonl
  python3 eval.py evalset.jsonl --retrieval-only        # skip generation (fast)
  python3 eval.py evalset.jsonl --base http://localhost:8001 --model dolphin-8b
"""
import sys, json, argparse, urllib.request

def post(base, path, body, timeout=180):
    req = urllib.request.Request(base.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def retrieve(base, collection, query):
    d = post(base, "/v1/tools/semantic-search",
             {"query": query, "context_filter": {"collection": collection}}, 60)
    return json.dumps(d)

def answer(base, model, collection, question):
    d = post(base, "/v1/messages", {
        "model": model, "max_tokens": 300,
        "messages": [{"role": "user", "content": question}],
        "tools": [{"name": "semantic_search", "type": "semantic_search_v1",
                   "context": [{"type": "ingested_artifact",
                                "context_filter": {"collection": collection}}],
                   "inputSchema": {"type": "object",
                                   "properties": {"query": {"type": "string"}},
                                   "required": ["query"]},
                   "tool_choice": {"type": "tool", "name": "semantic_search"}}]})
    blocks = d.get("content", [])
    return " ".join(b.get("text", "") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "text")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("evalset")
    ap.add_argument("--base", default="http://localhost:8001")
    ap.add_argument("--model", default="dolphin-8b")
    ap.add_argument("--retrieval-only", action="store_true")
    a = ap.parse_args()

    items = [json.loads(l) for l in open(a.evalset) if l.strip()]
    r_tot = r_hit = a_tot = a_hit = 0
    for i, it in enumerate(items, 1):
        q, coll = it["question"], it.get("collection", "default")
        line = "[%d] %s" % (i, q[:60])
        try:
            if it.get("expect_retrieved"):
                r_tot += 1
                ok = it["expect_retrieved"].lower() in retrieve(a.base, coll, q).lower()
                r_hit += ok; line += "  | retr:%s" % ("HIT" if ok else "MISS")
            if it.get("expect") and not a.retrieval_only:
                a_tot += 1
                ans = answer(a.base, a.model, coll, q)
                ok = it["expect"].lower() in ans.lower()
                a_hit += ok; line += "  | ans:%s" % ("OK" if ok else "WRONG")
        except Exception as e:
            line += "  | ERROR %s" % e
        print(line)

    print("\n--- summary ---")
    if r_tot: print("retrieval hit-rate: %d/%d = %.0f%%" % (r_hit, r_tot, 100*r_hit/r_tot))
    if a_tot: print("answer accuracy:    %d/%d = %.0f%%" % (a_hit, a_tot, 100*a_hit/a_tot))
    if not (r_tot or a_tot): print("(no checks — add 'expect'/'expect_retrieved' to items)")

if __name__ == "__main__":
    main()
