# expect:
# xyz
print('abc'.translate(str.maketrans('abc', 'xyz')))
# asmpython (beta/3.14.0) rejects at compile: [E113] type has no method 'maketrans'
