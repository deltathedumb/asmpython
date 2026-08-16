# probes: send() delivers a value into the generator
# expect:
# ready
# got:7
def echo():
    received = yield "ready"
    yield "got:" + str(received)


gen = echo()
print(next(gen))
print(gen.send(7))
