# while with walrus
items = [10, 20, 30]
idx = 0
while (val := items[idx]) < 25:
    print(val)
    idx += 1
