# expect:
# ABC
def chars():
    for c in 'abc':
        yield c.upper()
print(''.join(chars()))
# asmpython (beta/3.14.0) rejects at compile: [E022] str.join() requires list[str]
