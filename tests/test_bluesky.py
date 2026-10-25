from types import SimpleNamespace

from atproto_client.exceptions import BadRequestError, InvokeTimeoutError

import bluesky
from bluesky import TID_ALPHABET, generate_tid, get_post_texts_since, parse_urls, parse_facets, post_to_bluesky


def test_parse_urls_extracts_url_and_offsets():
    text = "Link: https://example.com here"
    spans = parse_urls(text)
    assert len(spans) == 1
    span = spans[0]
    assert span["url"] == "https://example.com"
    # The reported byte offsets should slice back to the url.
    assert text.encode("UTF-8")[span["start"]:span["end"]].decode() == "https://example.com"


def test_parse_urls_none_present():
    assert parse_urls("no links in this text") == []


def test_parse_facets_builds_link_facet():
    facets = parse_facets("see https://example.com/path now")
    assert len(facets) == 1
    facet = facets[0]
    assert facet["features"][0]["$type"] == "app.bsky.richtext.facet#link"
    assert facet["features"][0]["uri"] == "https://example.com/path"
    assert facet["index"]["byteStart"] < facet["index"]["byteEnd"]


def test_generate_tid_format_and_ordering():
    first = generate_tid()
    second = generate_tid()
    assert len(first) == 13
    assert set(first) <= set(TID_ALPHABET)
    # TIDs are time-sortable: a later TID never sorts before an earlier one
    # (equal microseconds are possible but differ only in the random clock id).
    assert first[:11] <= second[:11]


class _FakeRepo:
    def __init__(self, fail_create_after_storing):
        self.records = {}
        self.create_calls = 0
        self.fail_create_after_storing = fail_create_after_storing

    def upload_blob(self, data):
        return SimpleNamespace(blob="blob")

    def create_record(self, data):
        self.create_calls += 1
        rkey = data["rkey"]
        if rkey in self.records:
            raise BadRequestError()
        self.records[rkey] = {"uri": f"at://did/post/{rkey}", "cid": "cid"}
        if self.fail_create_after_storing:
            # Simulate the post being saved but the response timing out.
            self.fail_create_after_storing = False
            raise InvokeTimeoutError()
        return self.records[rkey]

    def get_record(self, params):
        if params["rkey"] not in self.records:
            raise BadRequestError()
        return self.records[params["rkey"]]


def _install_fake_client(monkeypatch, repo):
    class FakeClient:
        def __init__(self):
            self.com = SimpleNamespace(atproto=SimpleNamespace(repo=repo))
            self.me = SimpleNamespace(did="did:plc:test")

        def login(self, username, password):
            pass

        def get_current_time_iso(self):
            return "2026-01-01T00:00:00Z"

    monkeypatch.setattr(bluesky, "Client", FakeClient)
    monkeypatch.setattr(bluesky.time, "sleep", lambda s: None)


def test_post_retry_after_timeout_does_not_double_post(monkeypatch):
    repo = _FakeRepo(fail_create_after_storing=True)
    _install_fake_client(monkeypatch, repo)

    response = post_to_bluesky("user", "pass", "hello")

    assert len(repo.records) == 1
    assert repo.create_calls == 1
    assert response == next(iter(repo.records.values()))


def test_post_succeeds_first_try(monkeypatch):
    repo = _FakeRepo(fail_create_after_storing=False)
    _install_fake_client(monkeypatch, repo)

    post_to_bluesky("user", "pass", "hello")

    assert len(repo.records) == 1
    assert repo.create_calls == 1


def _feed_item(text, created_at, repost=False):
    item = {"post": {"record": {"text": text, "createdAt": created_at}}}
    if repost:
        item["reason"] = {"$type": "app.bsky.feed.defs#reasonRepost"}
    return item


def test_get_post_texts_since_stops_at_cutoff(monkeypatch):
    pages = {
        None: {
            "feed": [
                _feed_item("new", "2026-10-27T10:00:00.000Z"),
                _feed_item("reposted", "2026-10-27T09:00:00.000Z", repost=True),
                _feed_item("cutoff day", "2026-10-25T01:00:00.000Z"),
            ],
            "cursor": "page2",
        },
        "page2": {
            "feed": [_feed_item("too old", "2026-10-24T23:00:00.000Z")],
            "cursor": "page3",
        },
    }
    requested = []

    def fake_get(url, params, timeout):
        cursor = params.get("cursor")
        requested.append(cursor)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: pages[cursor])

    monkeypatch.setattr(bluesky.httpx, "get", fake_get)

    assert get_post_texts_since("everylot.det.city", "2026-10-25") == ["new", "cutoff day"]
    # Stops after the page that reaches past the cutoff; never asks for page3.
    assert requested == [None, "page2"]
