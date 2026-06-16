# probe18: dict comprehension, set comprehension, multiple assignment, global
total: int = 0

def add_to_total(n: int) -> None:
    global total
    total += n

add_to_total(5)
add_to_total(3)
print(total)

# dict comprehension
keys = ["a", "b", "c"]
vals = [1, 2, 3]
d = {k: v for k, v in zip(keys, vals)}
print(d.get("b"))
print(d.get("c"))

# set comprehension
ns = [1, 2, 2, 3, 3, 3]
s = {str(x) for x in ns}
print(len(s))

# multiple assignment targets
a = b = 0
a += 1
b += 2
print(a)
print(b)
