"""Downloader logic with a fake fetch function: no network access."""

import hashlib

import pytest

from src.discover.download import (
    DownloadError,
    ManifestFile,
    discover_new,
    download_all,
    ensure_file,
    validate_url,
)

PDF = b"%PDF-1.7 fake report"
URL = "https://www.darpg.gov.in/static/uploads/2025/10/abc.pdf"


def item(data=PDF, url=URL):
    return ManifestFile("cpgrams_centre_2025-08_r40_en.pdf", url, hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize(
    "url",
    [
        "http://www.darpg.gov.in/static/uploads/a.pdf",
        "https://evil.example/static/uploads/a.pdf",
        "https://www.darpg.gov.in/cms/wp-admin/a.pdf",
    ],
)
def test_only_official_https_upload_urls_are_allowed(url):
    with pytest.raises(DownloadError):
        validate_url(url)


def test_download_verifies_and_writes(tmp_path):
    calls = []
    assert ensure_file(item(), tmp_path, lambda u: calls.append(u) or PDF) == "downloaded"
    assert (tmp_path / item().filename).read_bytes() == PDF and calls == [URL]


def test_cached_file_is_not_downloaded_again(tmp_path):
    (tmp_path / item().filename).write_bytes(PDF)
    assert ensure_file(item(), tmp_path, lambda u: pytest.fail("network used")) == "cached"


def test_checksum_mismatch_is_not_saved(tmp_path):
    with pytest.raises(DownloadError, match="checksum"):
        ensure_file(item(), tmp_path, lambda u: b"%PDF changed upstream")
    assert not (tmp_path / item().filename).exists()


def test_non_pdf_response_is_rejected(tmp_path):
    with pytest.raises(DownloadError, match="not a PDF"):
        ensure_file(item(), tmp_path, lambda u: b"<html>error</html>")


def test_failures_are_reported_and_limit_is_respected(tmp_path):
    files = [item(), ManifestFile("b.pdf", URL, "0" * 64), ManifestFile("c.pdf", URL, "0" * 64)]
    results = download_all(files, tmp_path, lambda u: PDF, delay=0, limit=2)
    assert [r["status"] for r in results] == ["downloaded", "failed", "skipped_limit"]


def test_discover_lists_only_unknown_centre_entries():
    pages = {
        1: [
            {
                "id": 1,
                "date": "2026-10-13",
                "title": {"rendered": "CPGRAMS Monthly Report – Centre"},
                "acf": {"pdf": 10},
            },
            {
                "id": 2,
                "date": "2026-10-13",
                "title": {"rendered": "CPGRAMS Monthly Report – State"},
                "acf": {"pdf": 11},
            },
            {
                "id": 3,
                "date": "2026-11-12",
                "title": {"rendered": "CPGRAMS Monthly Report – Centre"},
                "acf": {"pdf": 12},
            },
        ]
    }

    def fetch(url):
        page = int(url.rsplit("page=", 1)[1])
        if page not in pages:
            raise DownloadError("HTTP 400")
        return pages[page]

    import src.discover.download as d

    d.DELAY_SECONDS, saved = 0, d.DELAY_SECONDS
    try:
        new, scanned = discover_new({10}, fetch_json=fetch)
    finally:
        d.DELAY_SECONDS = saved
    assert [e["post_id"] for e in new] == [3] and scanned == 3


def test_discover_fails_loudly_when_the_api_is_unreachable():
    def fetch(url):
        raise DownloadError("HTTP 403")

    with pytest.raises(DownloadError):
        discover_new(set(), fetch_json=fetch)
