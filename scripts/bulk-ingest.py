#!/usr/bin/env python3
"""bulk-ingest.py - ingest /home/USER/pgpt/documents into PrivateGPT.

One PrivateGPT collection per top-level subfolder of documents/ (files placed
directly in documents/ go to the 'default' collection). Idempotent: a sha256
manifest skips unchanged files and delete+re-ingests changed ones, so the
script is safe to re-run or cron.

Usage:
  python3 bulk-ingest.py                       # ingest new/changed files
  python3 bulk-ingest.py --dry-run             # show plan, no API calls
  python3 bulk-ingest.py --force               # re-ingest everything
  python3 bulk-ingest.py --collection handbook # only one collection
  python3 bulk-ingest.py --base URL --docs DIR
"""
import os, sys, json, base64, hashlib, argparse, urllib.request, urllib.error

SUPPORTED = {".pdf",".docx",".xlsx",".pptx",".txt",".md",".csv",
             ".png",".jpg",".jpeg",".tiff",".tif",".bmp",".gif"}
MAX_BLOB = 26214400  # server maximum_blob_size (25 MiB)

def sha256(path):
    h = hashlib.sha256()
    with open(path,"rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def load_manifest(p):
    m = {}
    if os.path.exists(p):
        with open(p) as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) == 3:
                    m[(parts[0], parts[1])] = parts[2]
    return m

def save_manifest(p, m):
    tmp = p + ".tmp"
    with open(tmp,"w") as f:
        for (coll,art),dig in sorted(m.items()):
            f.write("%s\t%s\t%s\n" % (coll,art,dig))
    os.replace(tmp, p)

def api(base, path, body, timeout=300):
    data = json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/")+path, data=data,
            headers={"Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("PGPT_BASE","http://localhost:8001"))
    ap.add_argument("--docs", default="/home/USER/pgpt/documents")
    ap.add_argument("--manifest", default="/home/USER/pgpt/.ingest-manifest.tsv")
    ap.add_argument("--collection", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    docs = os.path.abspath(a.docs)
    mname = os.path.basename(a.manifest)
    manifest = load_manifest(a.manifest)
    ingested = skipped = failed = unsupported = updated = 0

    for root, dirs, files in os.walk(docs):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in sorted(files):
            if fn.startswith(".") or fn in (mname, "README.txt"):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, docs)
            parts = rel.split(os.sep)
            coll = "default" if len(parts)==1 else parts[0]
            art  = parts[0] if len(parts)==1 else os.sep.join(parts[1:])
            if a.collection and coll != a.collection:
                continue
            if os.path.splitext(fn)[1].lower() not in SUPPORTED:
                print("  SKIP unsupported: %s" % rel); unsupported += 1; continue
            size = os.path.getsize(full)
            if size > MAX_BLOB:
                print("  SKIP too-large: %s (%d MiB > 25 MiB limit)" % (rel, size//1048576))
                failed += 1; continue
            dig = sha256(full)
            key = (coll, art)
            prev = manifest.get(key)
            if prev == dig and not a.force:
                skipped += 1; continue
            action = ("re-ingest(forced)" if a.force and prev else
                      "re-ingest(changed)" if prev else "ingest(new)")
            print("  %-19s [%s] %s (%d KiB)" % (action, coll, art, size//1024))
            if a.dry_run:
                continue
            try:
                if prev:
                    try: api(a.base,"/v1/artifacts/delete",{"collection":coll,"artifact":art},60)
                    except Exception: pass
                with open(full,"rb") as f:
                    b64 = base64.b64encode(f.read()).decode()
                st, resp = api(a.base, "/v1/artifacts/ingest",
                    {"artifact":art,"collection":coll,
                     "input":{"type":"file","value":b64},
                     "metadata":{"file_name":fn}})
                if st == 200:
                    manifest[key] = dig; ingested += 1
                    if prev: updated += 1
                else:
                    print("    FAIL http %s: %s" % (st, resp[:200])); failed += 1
            except urllib.error.HTTPError as e:
                print("    FAIL http %s: %s" % (e.code, e.read().decode()[:200])); failed += 1
            except Exception as e:
                print("    FAIL %s" % e); failed += 1

    if not a.dry_run:
        save_manifest(a.manifest, manifest)
    print("\nSummary: ingested=%d (updated=%d) skipped=%d unsupported=%d failed=%d%s"
          % (ingested, updated, skipped, unsupported, failed,
             " [DRY-RUN]" if a.dry_run else ""))
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
