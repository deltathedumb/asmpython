# tier: spec
# ref: peps.python.org/pep-0616/
# expect:
# Hook
# TestHook
# file
# file.txt
print('TestHook'.removeprefix('Test'))
print('TestHook'.removeprefix('Nope'))
print('file.txt'.removesuffix('.txt'))
print('file.txt'.removesuffix('.md'))
print(''.removeprefix('a'))
