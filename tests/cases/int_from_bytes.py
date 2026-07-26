# expect:
# 1024
print(int.from_bytes(b'\x04\x00', 'big'))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
