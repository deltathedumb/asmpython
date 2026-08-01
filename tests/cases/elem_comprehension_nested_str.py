# probes: a nested comprehension reads inner elements (str elements)
# expect:
# [['aa', 'bb', 'cc'], ['aa', 'bb', 'cc']]
rows = [["aa", "bb", "cc"], ["aa", "bb", "cc"]]
print([[v for v in row] for row in rows])
