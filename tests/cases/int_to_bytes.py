# expect:
# b'\x04\x00'
print((1024).to_bytes(2, 'big'))
# asmpython (beta/3.14.0) MISMATCH: prints '[4, 0]\n' (wrong).
