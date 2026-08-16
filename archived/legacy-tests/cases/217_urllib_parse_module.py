# expect:
# hello%20world
# hello world
# https
# example.com

from urllibparse import quote, unquote, urlparse

encoded: str = quote("hello world")
print(encoded)

decoded: str = unquote(encoded)
print(decoded)

result = urlparse("https://example.com/path?q=1")
print(result.scheme)
print(result.netloc)
