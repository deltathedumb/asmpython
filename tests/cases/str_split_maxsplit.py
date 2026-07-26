# expect:
# ['a', 'b', 'c,d']
print('a,b,c,d'.split(',', 2))
# asmpython (beta/3.14.0) MISMATCH: prints "['a', 'b', 'c', 'd']\n" (wrong).
