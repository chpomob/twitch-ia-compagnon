# Provenance of the `web` module's fixtures

Every file in this directory is written by hand for the tests in
`tests/test_web.py`. None is a capture of a live site or of a live provider
answer: each follows the shape public documentation describes, named below,
and holds only invented text.

| File | Shape it follows | Used by |
| --- | --- | --- |
| `page_basic.html` | WHATWG HTML Living Standard — §13.2 *Parsing HTML documents* (raw-text `script`/`style` elements, comments, named and numeric character references: <https://html.spec.whatwg.org/multipage/parsing.html>) and §4.2.2 *The `title` element* (<https://html.spec.whatwg.org/multipage/semantics.html#the-title-element>). A `<title>`, a `<style>`, a `<script>` of more than 5 KiB, a comment, `&eacute;` and `&#x263A;`, and three `<p>` blocks; each skipped part plants a `*_SENTINEL` string the tests assert is absent. | AC7 (P3) |
| `page_latin1.html` | WHATWG HTML Living Standard — §13.2.3 *The input byte stream*, the encoding sniffing algorithm: the transport-layer `charset` first, then the `<meta charset>` prescan of the first 1024 bytes (<https://html.spec.whatwg.org/multipage/parsing.html#the-input-byte-stream>), and the WHATWG Encoding Standard's labels (<https://encoding.spec.whatwg.org/#names-and-labels>). ISO-8859-1 bytes (`0xE9` for `é`, `0xE8` for `è`) under `<meta charset="iso-8859-1">`. | AC7 (P3) |
