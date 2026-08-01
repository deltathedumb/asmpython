# probes: a set comprehension reads each element (int elements)
# expect:
# ['10', '20', '30', '40']
xs = [10, 20, 30, 40]
print(sorted({str(v) for v in xs}))
