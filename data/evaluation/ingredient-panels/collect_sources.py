"""One bounded fetch per ingredient-panel photograph; no OCR, no alteration.

The existing real-photo sets are all front-of-pack or code close-ups, so they
cannot exercise ingredient reading at all. These are real photographs of real
ingredient panels, all under a licence that permits redistribution, so the
evaluation stays reproducible from a public checkout.

Every one of them is a European or North American package. That is a stated
limitation of this set, not an oversight: no comparably licensed photograph of
an Indian ingredient panel was found. Indian labels print the same allergen
vocabulary in English, but a set drawn from them would be the better test.
"""
import hashlib
import json
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent
COMMONS = "https://commons.wikimedia.org/wiki/"
SOURCES = [
    {"id": "scrapple-can-back", "filename": "scrapple-can-back.jpg",
     "commons_title": "File:Reese Philadelphia Canned Scrapple - back of can with ingredient list.jpg",
     "author": "Loudfan", "license": "CC BY-SA 4.0"},
    {"id": "white-pudding", "filename": "white-pudding.jpg",
     "commons_title": "File:White pudding ingredients label.JPG",
     "author": "O'Dea", "license": "CC BY-SA 4.0"},
    {"id": "marrowfat-peas", "filename": "marrowfat-peas.jpg",
     "commons_title": "File:Euroshopper canned marrowfat peas ingredient list.jpg",
     "author": "Not separately identified on the file page", "license": "CC BY 4.0"},
    {"id": "tomato-soup", "filename": "tomato-soup.jpg",
     "commons_title": "File:Tomato soup in a can ingredient list.jpg",
     "author": "Not separately identified on the file page", "license": "CC BY 4.0"},
    {"id": "confectionery-tartrazine", "filename": "confectionery-tartrazine.jpg",
     "commons_title": "File:Ingredients and tartrazine warning on confectionery label.jpg",
     "author": "Sokolikmawwer0", "license": "CC BY-SA 3.0"},
    {"id": "tortilla-chips", "filename": "tortilla-chips.png",
     "commons_title": "File:Ingredients label for tortilla chips .png",
     "author": "Cindyy28", "license": "CC BY-SA 4.0"},
    {"id": "habanero-condiment", "filename": "habanero-condiment.jpg",
     "commons_title": "File:Yellowbird Habanero Condiment ingredients label.jpg",
     "author": "Xyzerb", "license": "CC0"},
]
THROTTLE_SECONDS = 6  # Commons answered an unspaced run with HTTP 429.
AGENT = "TulaQA/0.1 (Legal Metrology label research; single bounded fetch per file)"


def direct_url(title: str) -> str:
    query = urllib.parse.urlencode({
        "action": "query", "titles": title, "prop": "imageinfo",
        "iiprop": "url|size|mime|sha1", "format": "json",
    })
    request = urllib.request.Request(
        "https://commons.wikimedia.org/w/api.php?" + query, headers={"User-Agent": AGENT}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    page = next(iter(payload["query"]["pages"].values()))
    return page["imageinfo"][0]["url"].split("?")[0]


def fetch(source: dict) -> dict:
    record = {**source,
              "source_page": COMMONS + urllib.parse.quote(source["commons_title"].replace(" ", "_")),
              "accessed_at": datetime.now(UTC).isoformat(timespec="seconds"),
              "local_modifications": "None; original source bytes retained"}
    target = ROOT / source["filename"]
    try:
        if target.exists():
            # Reruns are for the files a rate limit turned away. An original
            # already on disk is never refetched and never overwritten.
            payload = target.read_bytes()
            with Image.open(target) as image:
                record["dimensions"] = list(image.size)
                record["format"] = image.format
            record["bytes"] = len(payload)
            record["sha256"] = hashlib.sha256(payload).hexdigest()
            record["status"] = "retained"
            record["note"] = "already present; not refetched"
            return record
        time.sleep(THROTTLE_SECONDS)
        url = direct_url(source["commons_title"])
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=40) as response:
            payload = response.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("Source exceeds the 20 MB fetch limit")
        target.write_bytes(payload)
        with Image.open(target) as image:
            record["dimensions"] = list(image.size)
            record["format"] = image.format
        record["original_url"] = url
        record["bytes"] = len(payload)
        record["sha256"] = hashlib.sha256(payload).hexdigest()
        record["status"] = "retained"
    except Exception as error:  # noqa: BLE001 - the manifest records every failure verbatim
        record["status"] = "failed"
        record["error"] = f"{type(error).__name__}: {error}"
    return record


def main() -> None:
    records = [fetch(source) for source in SOURCES]
    (ROOT / "sources.json").write_text(json.dumps({
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provenance": (
            "Wikimedia Commons, fetched once each through the Commons API. Every "
            "file carries a licence permitting redistribution with attribution; "
            "the attribution is recorded here and must travel with the images. "
            "None of these photographers or publishers endorses Tula."
        ),
        "sources": records,
    }, indent=2), encoding="utf-8")
    for record in records:
        print(f"{record['status']:8s} {record['filename']:28s} {record.get('error', record.get('sha256', '')[:16])}")


if __name__ == "__main__":
    main()
