#!/home/USER/marker-venv/bin/python
"""preprocess.py - convert documents_raw/ -> documents/ for RAG ingestion.

Uses marker (marker-pdf) to turn PDFs (and images) into clean Markdown, which
gives the embedder structured text and far better chunking than raw-PDF parsing.
Collection layout is preserved: documents_raw/<collection>/x.pdf becomes
documents/<collection>/x.md. Text/markdown files are copied through unchanged;
other office formats are passed through for PrivateGPT's own parsers.

Idempotent: a sha256 manifest skips unchanged sources. Marker models load once,
lazily (only if there's a PDF/image to convert). Runs on CPU by default.

Run:  /home/USER/marker-venv/bin/python /home/USER/pgpt/preprocess.py
Flags: --dry-run  --force  --collection NAME  --device cpu|cuda
Then:  python3 /home/USER/pgpt/bulk-ingest.py
"""
import os, sys, shutil, json, hashlib, argparse

CONVERT = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif"}  # -> marker -> .md
COPY    = {".md", ".txt"}                                                     # already text
PASS    = {".docx", ".xlsx", ".pptx", ".csv"}                                # let PrivateGPT parse

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()

def load_manifest(p):
    m = {}
    if os.path.exists(p):
        for line in open(p):
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 2:
                m[parts[0]] = parts[1]
    return m

def save_manifest(p, m):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        for k, v in sorted(m.items()):
            f.write("%s\t%s\n" % (k, v))
    os.replace(tmp, p)

_converter = None
def get_converter(device):
    global _converter
    if _converter is None:
        os.environ.setdefault("TORCH_DEVICE", device)
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        print("  [marker] loading models (device=%s, first run downloads weights)..." % device, flush=True)
        _converter = PdfConverter(artifact_dict=create_model_dict())
    return _converter

def convert_pdf(src, device):
    from marker.output import text_from_rendered
    rendered = get_converter(device)(str(src))
    text, _ext, _imgs = text_from_rendered(rendered)
    return text

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="/home/USER/pgpt/documents_raw")
    ap.add_argument("--out", default="/home/USER/pgpt/documents")
    ap.add_argument("--manifest", default="/home/USER/pgpt/.preprocess-manifest.tsv")
    ap.add_argument("--collection", default=None)
    ap.add_argument("--device", default=os.environ.get("TORCH_DEVICE", "cpu"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    raw = os.path.abspath(a.raw)
    out = os.path.abspath(a.out)
    man = load_manifest(a.manifest)
    converted = copied = passed = skipped = failed = unsupported = 0

    for root, dirs, files in os.walk(raw):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in sorted(files):
            if fn.startswith(".") or fn == "README.txt":
                continue
            src = os.path.join(root, fn)
            rel = os.path.relpath(src, raw)
            parts = rel.split(os.sep)
            coll = "default" if len(parts) == 1 else parts[0]
            if a.collection and coll != a.collection:
                continue
            stem, ext = os.path.splitext(fn)
            ext = ext.lower()
            kind = ("convert" if ext in CONVERT else
                    "copy"    if ext in COPY else
                    "pass"    if ext in PASS else None)
            if kind is None:
                print("  SKIP unsupported: %s" % rel); unsupported += 1; continue

            dig = sha256(src)
            if man.get(rel) == dig and not a.force:
                skipped += 1; continue

            outname = (stem + ".md") if kind == "convert" else fn
            dst = os.path.join(out, coll, outname)
            print("  %-8s %s -> %s" % (kind.upper(), rel, os.path.relpath(dst, out)))
            if a.dry_run:
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if kind == "convert":
                    md = convert_pdf(src, a.device)
                    with open(dst, "w") as f:
                        f.write(md)
                    converted += 1
                else:
                    shutil.copy2(src, dst)
                    if kind == "copy": copied += 1
                    else: passed += 1
                man[rel] = dig
            except Exception as e:
                print("    FAIL %s: %s" % (rel, e)); failed += 1

    if not a.dry_run:
        save_manifest(a.manifest, man)
    print("\nSummary: converted=%d copied=%d passthrough=%d skipped=%d unsupported=%d failed=%d%s"
          % (converted, copied, passed, skipped, unsupported, failed,
             " [DRY-RUN]" if a.dry_run else ""))
    return 0 if failed == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
