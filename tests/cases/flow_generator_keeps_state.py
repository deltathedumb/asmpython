# probes: a generator resumes with its locals intact
# expect:
# [1, 3, 6]
def running_total(values):
    total = 0
    for v in values:
        total = total + v
        yield total


print(list(running_total([1, 2, 3])))
