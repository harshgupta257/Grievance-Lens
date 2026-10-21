"""Polite, cached, checksum-verified downloader for the report PDFs listed in data/manifest.csv.

Usage (PowerShell, project root, venv active):
    python -m src.discover.download              # fetch any missing PDFs, verify every cached one
    python -m src.discover.download --dry-run    # show what would be fetched
    python -m src.discover.download --limit 2    # fetch at most 2 files
    python -m src.discover.download --discover   # list CMS report entries not yet in the manifest

Rules (DESIGN.md D-001, D-003, D-008):
* only https URLs on www.darpg.gov.in/static/uploads/ are fetched; sequential, with a delay between requests;
* a cached file whose SHA-256 matches the manifest is never downloaded again;
* a downloaded file is kept only if it starts with %PDF and its SHA-256 matches the manifest, otherwise it
  is discarded and the failure is reported (a changed upstream file needs human review, not a silent update);
* --discover only *reports* new entries: a new report is added to the manifest by a person after its month
  and number have been checked on the cover page.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.config import PROJECT_ROOT

MANIFEST = PROJECT_ROOT / "data" / "manifest.csv"
PDF_DIR = PROJECT_ROOT / "data" / "raw" / "pdfs"
LOG = PROJECT_ROOT / "data" / "interim" / "download_log.csv"
ALLOWED_HOST = "www.darpg.gov.in"
ALLOWED_PATH_PREFIX = "/static/uploads/"
CMS_API = "https://www.darpg.gov.in/cms/wp-json/wp/v2"
USER_AGENT = "cpgrams-analytics/0.1 (portfolio research project; polite sequential downloads)"
DELAY_SECONDS = 3.0
TIMEOUT_SECONDS = 60
RETRIES = 3

Fetch = Callable[[str], bytes]


class DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ManifestFile:
    filename: str
    source_url: str
    sha256: str


def validate_url(url: str) -> None:
    """Only official DARPG upload URLs over https are allowed."""
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != ALLOWED_HOST
        or not parts.path.startswith(ALLOWED_PATH_PREFIX)
    ):
        raise DownloadError(f"refusing URL outside https://{ALLOWED_HOST}{ALLOWED_PATH_PREFIX}: {url}")


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def http_get(url: str, *, retries: int = RETRIES, timeout: int = TIMEOUT_SECONDS) -> bytes:
    """GET with exponential backoff on network errors and 5xx/429. 4xx (other than 429) fails at once."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL validated by callers
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code < 500 and exc.code != 429:
                raise DownloadError(f"HTTP {exc.code} for {url}") from exc
            last = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
        time.sleep(2 ** (attempt + 1))
    raise DownloadError(f"giving up after {retries} attempts: {url} ({last})")


def load_manifest_files(manifest: Path = MANIFEST) -> list[ManifestFile]:
    """Files the project keeps: rows with a verified local copy (English and Hindi editions)."""
    with manifest.open(encoding="utf-8") as fh:
        return [
            ManifestFile(r["filename"], r["source_url"], r["checksum"].removeprefix("sha256:"))
            for r in csv.DictReader(fh)
            if r["download_status"] == "downloaded_verified_pdf"
        ]


def ensure_file(item: ManifestFile, directory: Path, fetch: Fetch, *, dry_run: bool = False) -> str:
    """Make sure `item` exists locally with the expected checksum. Returns a status word."""
    target = directory / item.filename
    if target.exists():
        if sha256_of(target.read_bytes()) == item.sha256:
            return "cached"
        raise DownloadError(
            f"{target.name}: local file does not match the manifest checksum; "
            "move it aside and re-run to download a fresh copy"
        )
    validate_url(item.source_url)
    if dry_run:
        return "would_download"
    data = fetch(item.source_url)
    if not data.startswith(b"%PDF"):
        raise DownloadError(f"{item.filename}: response is not a PDF")
    digest = sha256_of(data)
    if digest != item.sha256:
        raise DownloadError(
            f"{item.filename}: checksum {digest[:12]}… differs from the manifest "
            f"({item.sha256[:12]}…); upstream file changed, not saved"
        )
    directory.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(target)  # atomic: a crash never leaves a truncated PDF under the final name
    return "downloaded"


def download_all(
    files: list[ManifestFile],
    directory: Path = PDF_DIR,
    fetch: Fetch = http_get,
    *,
    delay: float = DELAY_SECONDS,
    limit: int | None = None,
    dry_run: bool = False,
) -> list[dict]:
    results, fetched = [], 0
    for item in files:
        needs_fetch = not (directory / item.filename).exists()
        if needs_fetch and limit is not None and fetched >= limit:
            results.append({"filename": item.filename, "status": "skipped_limit", "detail": ""})
            continue
        try:
            status = ensure_file(item, directory, fetch, dry_run=dry_run)
            detail = ""
        except DownloadError as exc:
            status, detail = "failed", str(exc)
        results.append({"filename": item.filename, "status": status, "detail": detail})
        if status in ("downloaded", "failed") and needs_fetch:
            fetched += 1
            time.sleep(delay)  # be polite between network requests
    return results


def discover_new(
    known_media_ids: set[int], fetch_json: Callable[[str], list] | None = None
) -> tuple[list[dict], int]:
    """CMS report entries ("monthly report", Centre-like titles) whose PDF media id is not in the manifest.

    Returns (new entries, number of entries scanned). Raises DownloadError if the first page cannot be read,
    so an unreachable API is never reported as "nothing new"."""
    fetch_json = fetch_json or (lambda url: json.loads(http_get(url)))
    out, page, scanned = [], 1, 0
    while True:
        url = f"{CMS_API}/central_documents?search=monthly%20report&per_page=100&page={page}"
        try:
            batch = fetch_json(url)
        except DownloadError:
            if page == 1:
                raise
            break  # past the last page (the API answers 400)
        if not batch:
            break
        scanned += len(batch)
        for entry in batch:
            acf = entry.get("acf") or {}
            title = entry.get("title", {}).get("rendered", "")
            ids = [i for i in (acf.get("pdf"), acf.get("pdf_hindi"), acf.get("pdf_both")) if i]
            centre_like = (
                any(k in title.lower() for k in ("centre", "central", "ministr"))
                and "state" not in title.lower()
            )
            if centre_like and not any(int(i) in known_media_ids for i in ids):
                out.append(
                    {
                        "post_id": entry.get("id"),
                        "date": entry.get("date", "")[:10],
                        "title": title,
                        "media_ids": ids,
                    }
                )
        page += 1
        time.sleep(DELAY_SECONDS)
    return out, scanned


def _write_log(results: list[dict]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    new = not LOG.exists()
    with LOG.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["timestamp", "filename", "status", "detail"])
        if new:
            w.writeheader()
        for r in results:
            w.writerow({"timestamp": stamp, **r})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download and verify the report PDFs in data/manifest.csv.")
    parser.add_argument("--limit", type=int, help="download at most N missing files")
    parser.add_argument("--dry-run", action="store_true", help="show what would be downloaded")
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS, help="seconds between requests")
    parser.add_argument("--discover", action="store_true", help="list CMS entries not in the manifest")
    args = parser.parse_args(argv)

    if args.discover:
        with MANIFEST.open(encoding="utf-8") as fh:
            known = {int(r["cms_media_id"]) for r in csv.DictReader(fh) if r["cms_media_id"]}
        try:
            new, scanned = discover_new(known)
        except DownloadError as exc:
            print(f"Discovery failed: {exc}", file=sys.stderr)
            return 1
        for e in new:
            print(f"NEW  post {e['post_id']}  {e['date']}  media {e['media_ids']}  {e['title']}")
        print(
            f"Scanned {scanned} CMS entries; {len(new)} candidate entries not in the manifest. "
            "Verify month/number on the cover before adding."
        )
        return 0

    results = download_all(load_manifest_files(), delay=args.delay, limit=args.limit, dry_run=args.dry_run)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] == "failed":
            print(f"FAILED {r['filename']}: {r['detail']}", file=sys.stderr)
    if not args.dry_run:
        _write_log(results)
    print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
