# expect:
# ['a\n', 'b\n']
print('a\nb\n'.splitlines(keepends=True))
# asmpython (beta/3.14.0) MISMATCH: prints "['a', 'b']\n" (wrong).
