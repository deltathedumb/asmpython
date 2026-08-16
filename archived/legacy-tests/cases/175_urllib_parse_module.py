# expect:
# hello%20world
# hello world
# http
# example.com
# /path

from urllib.parse import quote, unquote, urlparse

print(quote("hello world"))
print(unquote("hello%20world"))

r = urlparse("http://example.com/path?q=1")
print(r.scheme)
print(r.netloc)
print(r.path)
