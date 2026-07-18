# ext: no_global_mutation
# expect-error: without a 'global counter' declaration

counter = 5

def bump_counter() -> None:
    counter = counter + 1

bump_counter()
print(counter)
