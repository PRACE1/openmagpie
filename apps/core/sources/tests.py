from unittest import mock

import feedparser
from django.test import SimpleTestCase

from openmagpie_schema.configs import RssSourceSpec
from sources.connectors.rss.connector import RssConnector, _unwrap_xml_viewer

# A minimal RSS feed and the Chromium XML-viewer HTML wrapper FlareSolverr
# returns when its headless browser "renders" that feed (the original source
# lands inside <div id="webkit-xml-viewer-source-xml">).
_FEED_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>T</title>'
    "<item><title>One</title><guid>g1</guid>"
    "<pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate></item>"
    "<item><title>Two</title><guid>g2</guid>"
    "<pubDate>Tue, 02 Jun 2026 00:00:00 GMT</pubDate></item>"
    "</channel></rss>"
)
_WRAPPER = (
    '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
    '<style id="xml-viewer-style">div.header { color: red; }</style></head>'
    '<body><div id="webkit-xml-viewer-source-xml">' + _FEED_XML + "</div>"
    '<div class="pretty-print">&lt;rss&gt; tree view</div></body></html>'
)


class UnwrapXmlViewerTests(SimpleTestCase):
    """`_unwrap_xml_viewer` recovers the embedded feed from the Chromium
    XML-viewer wrapper, and leaves a non-wrapper body alone."""

    def test_extracts_embedded_feed_that_then_parses(self) -> None:
        xml = _unwrap_xml_viewer(_WRAPPER.encode("utf-8"))
        parsed = feedparser.parse(xml)
        self.assertEqual(parsed.version, "rss20")
        self.assertEqual([e.title for e in parsed.entries], ["One", "Two"])

    def test_raw_feed_passes_through_unchanged(self) -> None:
        raw = _FEED_XML.encode("utf-8")
        self.assertEqual(_unwrap_xml_viewer(raw), raw)

    def test_non_feed_html_passes_through_unchanged(self) -> None:
        # No marker -> not a viewer wrapper -> returned as-is (the caller's
        # feedparser then fails it as before; we don't fabricate a feed).
        html = b"<html><body>rate limited</body></html>"
        self.assertEqual(_unwrap_xml_viewer(html), html)

    def test_cdata_with_literal_close_div_is_not_truncated(self) -> None:
        # A feed item whose CDATA body contains a literal `</div>` must not
        # truncate extraction (why we slice to the LAST </rss>, not the
        # source div's </div>).
        feed = (
            '<rss version="2.0"><channel><title>T</title>'
            "<item><title>One</title><guid>g1</guid>"
            "<pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate>"
            "<description><![CDATA[<div>hello</div>]]></description></item>"
            "<item><title>Two</title><guid>g2</guid>"
            "<pubDate>Tue, 02 Jun 2026 00:00:00 GMT</pubDate></item>"
            "</channel></rss>"
        )
        wrapper = '<html><body><div id="webkit-xml-viewer-source-xml">' + feed + "</div></body></html>"
        parsed = feedparser.parse(_unwrap_xml_viewer(wrapper.encode("utf-8")))
        self.assertEqual([e.title for e in parsed.entries], ["One", "Two"])


class ChallengeBypassRecoveryTests(SimpleTestCase):
    """The RSS connector recovers a challenge-gated XML feed end to end:
    the direct fetch yields a non-feed body, the FlareSolverr fallback
    returns the XML-viewer wrapper, and the connector unwraps + parses it."""

    def test_poll_recovers_via_unwrapped_bypass_body(self) -> None:
        spec = RssSourceSpec(kind="rss", url="https://gated.example/feed", name="Gated")
        with (
            # Direct fetch hits the WAF gate: empty body -> no feed detected.
            mock.patch.object(RssConnector, "_fetch_with_ssl_fallback", return_value=b""),
            # FlareSolverr solves the challenge but returns the viewer wrapper.
            mock.patch.object(RssConnector, "challenge_bypass_fetch", return_value=_WRAPPER.encode("utf-8")),
        ):
            payloads = list(RssConnector().poll(spec, since=None))
        self.assertEqual([p.title for p in payloads], ["One", "Two"])
