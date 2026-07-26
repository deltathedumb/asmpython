# expect:
# [0, 6, 12, 18]
print([x for x in range(20) if x % 2 == 0 if x % 3 == 0])
# asmpython (beta/3.14.0) rejects at compile: [P002] expected OP ']', got KEYWORD 'if'
