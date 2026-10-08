import json
from datetime import UTC, datetime

import httpx
import pytest

from aleph.osint import _http
from aleph.osint.errors import InvalidInputError, RateLimitedError
from aleph.osint.wayback import (
    analyze_url,
    closest_snapshot,
    list_captures,
)

from .helpers import json_response, mock_client, text_response

HEADER = ["timestamp", "original", "mimetype", "statuscode", "digest", "length"]
ROWS = [
    ["20100101000000", "http://example.com:80/", "text/html", "200", "AAA111", "1000"],
    ["20100201000000", "http://example.com:80/", "text/html", "200", "AAA111", "1000"],
    ["20100301000000", "http://example.com:80/", "text/html", "200", "BBB222", "1200"],
    ["20100401000000", "http://example.com:80/", "text/html", "200", "BBB222", "1200"],
    ["20100501000000", "http://example.com:80/", "text/html", "200", "AAA111", "1000"],
]


def _cdx_body(rows):
    return json.dumps([HEADER] + rows)


async def test_full_history_first_last_and_digest_changes():
    seen = []

    def handler(request: httpx.Request):
        seen.append(dict(request.url.params))
        return text_response(_cdx_body(ROWS))

    async with mock_client(handler) as client:
        report = await analyze_url("example.com", client=client)

    assert seen[0]["url"] == "example.com"
    assert seen[0]["output"] == "json"
    assert seen[0]["limit"] == "5000"  # tope por defecto de capturas
    assert "digest" in seen[0]["fl"]
    assert report.captures_fetched == 5
    assert report.truncated is False
    assert report.first_capture.timestamp == datetime(2010, 1, 1, tzinfo=UTC)
    assert report.last_capture.timestamp == datetime(2010, 5, 1, tzinfo=UTC)
    assert report.distinct_digests == 2
    # Cambios de digest entre capturas consecutivas: AAA→BBB el 2010-03-01 y BBB→AAA el 2010-05-01
    assert [(c.previous_digest, c.digest) for c in report.changes] == [("AAA111", "BBB222"), ("BBB222", "AAA111")]
    assert report.changes[0].timestamp == datetime(2010, 3, 1, tzinfo=UTC)
    assert report.first_capture.archive_url == "https://web.archive.org/web/20100101000000/http://example.com:80/"
    assert report.entities[0].type == "url"
    assert report.entities[0].props["cambios_de_contenido"] == 2


async def test_truncated_history_fetches_newest_capture_separately():
    calls = []

    def handler(request: httpx.Request):
        limit = request.url.params["limit"]
        calls.append(limit)
        if limit == "-1":
            return text_response(_cdx_body([ROWS[-1]]))
        return text_response(_cdx_body(ROWS[:3]))

    async with mock_client(handler) as client:
        report = await analyze_url("example.com", client=client, max_captures=3)

    assert calls == ["3", "-1"]
    assert report.truncated is True
    assert report.captures_fetched == 3
    assert report.last_capture.timestamp == datetime(2010, 5, 1, tzinfo=UTC)
    assert [c.digest for c in report.changes] == ["BBB222"]
    assert any("primeras 3 capturas" in w for w in report.warnings)


async def test_empty_archive_body_means_no_captures():
    async with mock_client(lambda request: text_response("")) as client:
        report = await analyze_url("nunca-archivado.example", client=client)
    assert report.captures_fetched == 0
    assert report.first_capture is None and report.last_capture is None
    assert report.changes == []


async def test_list_captures_newest_uses_negative_limit():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(dict(request.url.params))
        return text_response(_cdx_body([ROWS[-1]]))

    async with mock_client(handler) as client:
        captures = await list_captures("example.com", client=client, limit=1, newest=True)
    assert seen["limit"] == "-1"
    assert len(captures) == 1
    assert captures[0].statuscode == "200"


async def test_closest_snapshot_found_and_not_found():
    payload_found = {"url": "example.com", "archived_snapshots": {"closest": {
        "available": True, "url": "http://web.archive.org/web/20130919044612/http://example.com/",
        "timestamp": "20130919044612", "status": "200"}}}

    async with mock_client(lambda r: json_response(payload_found)) as client:
        snap = await closest_snapshot("example.com", timestamp="2013", client=client)
    assert snap is not None
    assert snap.available is True
    assert snap.status == "200"
    assert snap.timestamp == datetime(2013, 9, 19, 4, 46, 12, tzinfo=UTC)

    async with mock_client(lambda r: json_response({"archived_snapshots": {}})) as client:
        assert await closest_snapshot("nunca.example", client=client) is None


async def test_429_is_retried_then_succeeds():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        if len(attempts) == 1:
            return text_response("slow down", status=429, headers={"Retry-After": "0"})
        return text_response(_cdx_body(ROWS[:1]))

    async with mock_client(handler) as client:
        captures = await list_captures("example.com", client=client)
    assert len(attempts) == 2
    assert len(captures) == 1


async def test_persistent_429_raises_rate_limited_error():
    attempts = []

    def handler(request: httpx.Request):
        attempts.append(1)
        return text_response("", status=429, headers={"Retry-After": "0"})

    async with mock_client(handler) as client:
        with pytest.raises(RateLimitedError) as info:
            await list_captures("example.com", client=client)
    assert len(attempts) == _http.MAX_RETRIES + 1
    assert info.value.status_code == 429


async def test_retry_after_above_cap_fails_fast():
    def handler(request: httpx.Request):
        return text_response("", status=429, headers={"Retry-After": "3600"})

    async with mock_client(handler) as client:
        with pytest.raises(RateLimitedError):
            await list_captures("example.com", client=client)


async def test_invalid_inputs_are_rejected_without_requests():
    def handler(request: httpx.Request):  # pragma: no cover - no debe ejecutarse
        raise AssertionError("no debía consultarse la red")

    async with mock_client(handler) as client:
        with pytest.raises(InvalidInputError):
            await analyze_url("   ", client=client)
        with pytest.raises(InvalidInputError):
            await list_captures("example.com", client=client, from_ts="2010-01-01")
        with pytest.raises(InvalidInputError):
            await closest_snapshot("example.com", timestamp="20x0", client=client)
