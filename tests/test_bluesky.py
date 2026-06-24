from bluesky import parse_urls, parse_facets


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
