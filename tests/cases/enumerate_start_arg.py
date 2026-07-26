# expect:
# [(5, 'a'), (6, 'b')]
print(list(enumerate(['a', 'b'], 5)))
# enumerate(x, start) with a start arg fails ([E002] undefined function 'enumerate').
