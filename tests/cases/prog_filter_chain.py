# expect:
# [0, 6, 12, 18]
data = list(range(20))
result = [x for x in data if x % 2 == 0 if x % 3 == 0]
print(result)
# asmpython (beta/3.14.0) rejects at compile: [P002] expected OP ']', got KEYWORD 'if'
