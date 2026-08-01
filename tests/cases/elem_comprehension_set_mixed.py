# probes: a set comprehension reads each element (mixed elements)
# expect:
# ['1', '3.5', 'None', 'True', 'two']
xs = [1, "two", 3.5, True, None]
print(sorted({str(v) for v in xs}))
