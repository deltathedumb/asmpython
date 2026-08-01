# tier: spec
# ref: library/stdtypes.html#bytes.decode
# expect:
# UnicodeDecodeError
# '\ufffda'
# 'a'
# '\xffa'
# 2
b = b"\xff\x61"
try:
    b.decode("utf-8")
except UnicodeDecodeError:
    print("UnicodeDecodeError")
# ascii(), not print(), because the decoded text contains U+FFFD and a
# latin-1 high byte. stdout's encoding is environmental -- on a Windows
# console codepage the interpreter cannot encode U+FFFD at all, so printing
# it raises UnicodeEncodeError in the REFERENCE implementation and there is
# no expectation to record. The values are what the case is about; their
# console representation is not.
print(ascii(b.decode("utf-8", "replace")))
print(ascii(b.decode("utf-8", "ignore")))
print(ascii(b.decode("latin-1")))
print(len(b.decode("latin-1")))
