# probes: quote percent-escapes reserved characters
# expect:
# a%20b/c
# a b/c
from urllib.parse import quote, unquote

encoded = quote("a b/c")
print(encoded)
print(unquote(encoded))
