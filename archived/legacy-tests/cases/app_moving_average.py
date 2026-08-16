# expect:
# [2.0, 3.0, 4.0]
def moving_avg(data, window):
    result = []
    for i in range(len(data) - window + 1):
        chunk = data[i:i + window]
        result.append(sum(chunk) / window)
    return result
print(moving_avg([1, 2, 3, 4, 5], 3))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
