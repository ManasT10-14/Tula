"""Download explicitly selected public photographs; no OCR or label inference."""
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
SOURCES = [
    {"id": "commons-best-before", "filename": "best-before.png",
     "original_url": "https://upload.wikimedia.org/wikipedia/commons/b/bb/BestBeforeDate.png",
     "source_page": "https://commons.wikimedia.org/wiki/File:BestBeforeDate.png",
     "source_revision": "https://commons.wikimedia.org/w/index.php?title=File:BestBeforeDate.png&oldid=822349237",
     "author": "MasterOfHisOwnDomain", "license": "Public domain dedication by photographer",
     "license_url": "https://commons.wikimedia.org/wiki/File:BestBeforeDate.png#Licensing",
     "source_note": "Uploader identifies this as their own photograph of commercial packaging."},
    {"id": "commons-best-before-canada", "filename": "best-before-canada.jpg",
     "original_url": "https://upload.wikimedia.org/wikipedia/commons/6/65/Best_before_Canada.JPG",
     "source_page": "https://commons.wikimedia.org/wiki/File:Best_before_Canada.JPG",
     "author": "CambridgeBayWeather", "license": "CC BY-SA 3.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/3.0/",
     "source_note": "Uploader identifies this as their own photograph of a box bottom."},
    {"id": "commons-batch-mfg-exp", "filename": "batch-mfg-exp.jpg",
     "original_url": "https://upload.wikimedia.org/wikipedia/commons/0/0f/Batch_no%2C_MFG_Date_and_EXP_Date.jpg",
     "source_page": "https://commons.wikimedia.org/wiki/File:Batch_no,_MFG_Date_and_EXP_Date.jpg",
     "source_revision": "https://commons.wikimedia.org/w/index.php?title=File:Batch_no,_MFG_Date_and_EXP_Date.jpg&oldid=1116061359",
     "author": "Corn cheese", "license": "CC BY 4.0",
     "license_url": "https://creativecommons.org/licenses/by/4.0/",
     "source_note": "Uploader identifies this as their own photograph of a petroleum jelly container bottom."},
    {"id": "commons-manufacture-expiration", "filename": "manufacture-expiration.jpg",
     "original_url": "https://upload.wikimedia.org/wikipedia/commons/5/5e/Manufacture_Date_and_Expiration_Date.jpg",
     "source_page": "https://commons.wikimedia.org/wiki/File:Manufacture_Date_and_Expiration_Date.jpg",
     "source_revision": "https://commons.wikimedia.org/w/index.php?title=File:Manufacture_Date_and_Expiration_Date.jpg&oldid=1110151628",
     "author": "Gaurav Dhwaj Khadka", "license": "CC BY-SA 4.0",
     "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
     "source_note": "Uploader identifies this as their own photograph of a real product date marking."},
]


def download(source):
    result = {**source, "accessed_at": datetime.now(UTC).isoformat(), "modifications": "None; original downloaded bytes retained"}
    target = ROOT / source["filename"]
    try:
        if target.exists():
            raise ValueError("Destination already exists; original bytes were not overwritten")
        request = urllib.request.Request(source["original_url"], headers={"User-Agent": "TulaQA/0.1 (package-label research)"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read(20 * 1024 * 1024 + 1)
            if len(payload) > 20 * 1024 * 1024:
                raise ValueError("Image exceeds 20 MB collection bound")
            result["final_url"] = response.url
        with target.open("xb") as output:
            output.write(payload)
        with Image.open(target) as image:
            result["size"] = list(image.size)
            result["format"] = image.format
        result.update({"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload), "status": "downloaded"})
    except Exception as exc:
        result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    return result


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(download, SOURCES))
    manifest = {"dataset_id": "additional-real-labels-2026-09-07", "scope": "Single-agent QA photographs; not independent human or legal benchmark",
                "selection": "Direct photographer uploads on Wikimedia Commons; Open Food Facts web search was blocked by robots.txt",
                "images": results, "ocr_run_before_annotation": False}
    with (ROOT / "sources.json").open("x", encoding="utf-8") as output:
        json.dump(manifest, output, indent=2)
    print(json.dumps([{k: row.get(k) for k in ("id", "status", "size", "error")} for row in results], indent=2))
