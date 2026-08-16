# expect:
# 1,2,3
print(','.join(map(str, [1, 2, 3])))
# asmpython (beta/3.14.0) rejects at compile: [E022] str.join() requires list[str], got list[int]
