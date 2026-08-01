# probes: urlparse splits a URL into components
# expect:
# https
# example.com
# 8080
# /path
# q=1
# frag
from urllib.parse import urlparse

u = urlparse("https://example.com:8080/path?q=1#frag")
print(u.scheme)
print(u.hostname)
print(u.port)
print(u.path)
print(u.query)
print(u.fragment)
