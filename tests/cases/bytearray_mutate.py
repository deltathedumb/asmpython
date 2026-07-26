# expect:
# bytearray(b'xbc')
ba = bytearray(b'abc')
ba[0] = 120
print(ba)
# asmpython (beta/3.14.0) MISMATCH: prints '[120, 98, 99]\n' (wrong).
