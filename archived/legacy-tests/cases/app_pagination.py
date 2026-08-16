# expect:
# 4 [0, 1, 2] [9]
def paginate(items, page_size):
    pages = []
    for i in range(0, len(items), page_size):
        pages.append(items[i:i + page_size])
    return pages
result = paginate(list(range(10)), 3)
print(len(result), result[0], result[-1])
# asmpython (beta/3.14.0) MISMATCH: prints '4 8688816 8689072\n' (wrong).
