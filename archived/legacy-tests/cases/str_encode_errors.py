# expect:
# b'abc'
print('abc'.encode('ascii', 'ignore'))
# asmpython (beta/3.14.0) MISMATCH: prints '9999184\n' (wrong).
