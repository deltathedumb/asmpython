# ext: no_global_mutation
# expect:
# 25

def compute(n: int) -> int:
    result = n
    result = result + 0
    return result

print(compute(25))
