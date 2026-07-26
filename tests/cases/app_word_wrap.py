# expect:
# ['the quick', 'brown fox', 'jumps']
def wrap(text, width):
    words = text.split()
    lines = []
    current = ''
    for w in words:
        if len(current) + len(w) + 1 <= width:
            current = current + ' ' + w if current else w
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines
print(wrap('the quick brown fox jumps', 12))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
