# expect:
# a        1
# bb      22
# ccc    333
rows = [('a', 1), ('bb', 22), ('ccc', 333)]
for name, val in rows:
    print(f'{name:<5}{val:>5}')
# asmpython (beta/3.14.0) MISMATCH: prints 'a    1\nbb   22\nccc  333\n' (wrong).
