# probes: a generator body does not run until iterated
# expect:
# created
# started
# 1
def noisy():
    print("started")
    yield 1


gen = noisy()
print("created")
print(next(gen))
