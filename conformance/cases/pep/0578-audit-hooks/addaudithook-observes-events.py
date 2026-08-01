# tier: spec
# ref: library/sys.html#sys.addaudithook
# expect:
# ['conformance.test']
# True True
import sys

seen = []
sys.addaudithook(lambda event, args: seen.append(event) if event == "conformance.test" else None)
sys.audit("conformance.test", 1)
sys.audit("other.event", 1)
print(seen)
print(callable(sys.audit), callable(sys.addaudithook))
