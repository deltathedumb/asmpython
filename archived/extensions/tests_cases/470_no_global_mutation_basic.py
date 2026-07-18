# ext: no_global_mutation
# expect:
# 5
# 6

counter = 5

def read_counter() -> int:
    return counter

def bump_counter() -> None:
    global counter
    counter = counter + 1

print(read_counter())
bump_counter()
print(counter)
