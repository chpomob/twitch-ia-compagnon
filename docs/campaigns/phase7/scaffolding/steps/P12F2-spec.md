# P12F2 — One malformed search result must not discard its valid sibling

The closing gate reproduced a defect in `web.search`. R2 says a malformed individual result is dropped
and counted; today one bad item destroys the whole answer.

## The reproduction (from the gate report, F2)

Locations: `modules/web/__init__.py:1563` and `:1602` (the two backend loops), with the whole-payload
exception conversion in `_search`.

```python
wiki = {'query': {'search': [
    {'title': 'Valid', 'snippet': 'good'},
    {'title': '\ud800', 'snippet': 'bad'},
]}}
brave = {'web': {'results': [
    {'title': 'Valid', 'url': 'https://example.org', 'description': 'good'},
    {'title': '\ud800', 'url': 'https://example.org/bad', 'description': 'bad'},
]}}
```

Both observed `error malformed_response` with `result=None`: Wikipedia's URL encoding raises on the lone
surrogate, Brave's title-to-UTF-8 conversion raises, and the failure of one item is converted into a
failure of the whole answer.

## Required behaviour

- A result that cannot be used (unencodable text, malformed URL, missing required field) is **dropped**,
  the drop is **counted** in what the observation reports, and the remaining results are returned.
- An answer in which *every* item is unusable, or whose payload has no results array at all, is still
  `malformed_response` — dropping must not become "success with nothing".
- The same rule applies to both backends, and the count is part of the observation's documented fields.
- No exception escapes from an item; the item boundary is where the guard lives.

## Tests (exact inputs above, plus)

- Wikipedia and Brave each with one valid and one lone-surrogate item: `status == 'success'`, one result,
  one dropped, and the valid title preserved.
- All items malformed → `malformed_response`.
- A payload with the results array absent or of the wrong type → `malformed_response`.
- A payload of ten items where three are malformed → seven results, three dropped.
- The existing malformed-payload parametrizations must keep passing unchanged.

## Constraints

- `modules/web/__init__.py` and `tests/test_web.py` only.
- No test skipped, xfailed, deleted or loosened; full suite green with
  `.venv/bin/python -m pytest tests/ -q -p no:cacheprovider`.
- Reply with what changed, the test names, and the full-suite count.
