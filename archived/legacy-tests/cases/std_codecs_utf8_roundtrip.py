# probes: str.encode/bytes.decode round-trip UTF-8
# expect:
# 4
# 5
# True
text = "café"
encoded = text.encode("utf-8")
print(len(text))
print(len(encoded))
print(encoded.decode("utf-8") == text)
