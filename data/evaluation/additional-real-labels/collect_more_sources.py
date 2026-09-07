"""One bounded fetch per new primary-source photo/document; no OCR."""
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
SOURCES = [
    {"id": "nestle-india-tin", "filename": "nestle-india-tin.png", "type": "image",
     "original_url": "https://www.familynes.in/sites/default/files/inline-images/tin_pack_batch_code_1_optimized_450.png",
     "source_page": "https://www.familynes.in/nestle-india-know-your-product",
     "publisher": "Nestle India Limited / FamilyNes", "author": "Photographer not separately identified",
     "license": "Copyright Nestle or its licensors; no open license. Site permits extracts for private noncommercial use while retaining notices.",
     "license_url": "https://www.familynes.in/terms-conditions",
     "reuse_scope": "Local SIH development evaluation only; not a freely redistributable dataset or endorsement."},
    {"id": "nestle-india-box", "filename": "nestle-india-box.jpg", "type": "image",
     "original_url": "https://www.familynes.in/sites/default/files/inline-images/bib_pack_back_code_1.jpg",
     "source_page": "https://www.familynes.in/nestle-india-know-your-product",
     "publisher": "Nestle India Limited / FamilyNes", "author": "Photographer not separately identified",
     "license": "Copyright Nestle or its licensors; no open license. Site permits extracts for private noncommercial use while retaining notices.",
     "license_url": "https://www.familynes.in/terms-conditions",
     "reuse_scope": "Local SIH development evaluation only; not a freely redistributable dataset or endorsement."},
    {"id": "fssai-label-photo", "filename": "fssai-label-photo.pdf", "type": "pdf",
     "original_url": "https://hygiene.fssai.gov.in/files/docs/88894_pdf3.pdf",
     "source_page": "https://hygiene.fssai.gov.in/files/docs/88894_pdf3.pdf",
     "publisher": "FSSAI Hygiene Rating portal", "author": "Uploader/photographer not identified",
     "license": "No explicit image redistribution license established; government-hosted document is publicly accessible.",
     "reuse_scope": "Local analysis only; do not describe as openly licensed or infer regulatory approval."}
]


def fetch(source):
    result = {**source, "accessed_at": datetime.now(UTC).isoformat(), "local_modifications": "None; original source bytes retained"}
    target = ROOT / source["filename"]
    try:
        if target.exists():
            raise ValueError("Existing source will not be overwritten")
        request = urllib.request.Request(source["original_url"], headers={"User-Agent": "TulaQA/0.1 (package-label research)"})
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = response.read(20 * 1024 * 1024 + 1)
            if len(payload) > 20 * 1024 * 1024:
                raise ValueError("Source exceeds 20 MB bound")
            result["final_url"] = response.url
        target.write_bytes(payload)
        if source["type"] == "image":
            with Image.open(target) as image:
                result.update(size=list(image.size), format=image.format)
        result.update(status="downloaded", sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload))
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    return result


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(fetch, SOURCES))
    with (ROOT / "additional-sources.json").open("x", encoding="utf-8") as output:
        json.dump({"images": results, "ocr_run_before_annotation": False}, output, indent=2)
    print(json.dumps(results, indent=2))
