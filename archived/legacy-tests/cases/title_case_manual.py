# expect:
# The Quick Brown Fox
def title(s):
    words = s.split()
    result = []
    for w in words:
        result.append(w[0].upper() + w[1:])
    return ' '.join(result)
print(title('the quick brown fox'))
# asmpython (beta/3.14.0) rejects at compile: [E022] str.join() requires list[str], got list[?]
