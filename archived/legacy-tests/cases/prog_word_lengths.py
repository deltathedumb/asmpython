# expect:
# banana
words = ['apple', 'banana', 'kiwi']
lengths = {w: len(w) for w in words}
longest = max(lengths, key=lambda w: lengths[w])
print(longest)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
