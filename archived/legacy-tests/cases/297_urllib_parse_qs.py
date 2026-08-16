# expect:
# 2
# done

from urllib.parse import parse_qs, parse_qsl, urldefrag

pairs = parse_qsl("a=1&b=2")
print(len(pairs))
print("done")
