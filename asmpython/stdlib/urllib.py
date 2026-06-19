"""urllib package: URL handling.

Sub-modules available via dotted import:
  urllib.parse   -> urllibparse.py (always available)
  urllib.request -> urllib_request.py (HTTP/HTTPS client)
  urllib.error   -> urllib_error.py (exception classes)

Flat imports also work:
  from urllib import parse, request, error

Since asmpython can't resolve sub-package attributes dynamically, use
`from urllib.request import urlopen` rather than `urllib.request.urlopen`.
"""
from __future__ import annotations
