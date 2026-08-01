# probes: a set comprehension reads each element (float elements)
# expect:
# ['1.5', '2.5', '3.5', '4.5']
xs = [1.5, 2.5, 3.5, 4.5]
print(sorted({str(v) for v in xs}))
