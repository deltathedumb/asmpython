# expect:
# [('cat', 2), ('the', 2)]
def wc(text):
    counts = {}
    for word in text.lower().split():
        counts[word] = counts.get(word, 0) + 1
    return sorted(counts.items())
print(wc('The cat the CAT'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
