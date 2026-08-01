# tier: spec
# ref: library/stdtypes.html#bytes
# expect:
# b'Hello'
# b'  HELLO  ' b'  hello  '
# [b'Hello']
# b'  HeLLo  '
# 3
# b'ab' b'abab'
b = b"  Hello  "
print(b.strip())
print(b.upper(), b.lower())
print(b.split())
print(b.replace(b"l", b"L"))
print(b.find(b"e"))
print(b"a" + b"b", b"ab" * 2)
