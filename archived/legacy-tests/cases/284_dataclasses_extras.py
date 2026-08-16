# expect:
# frozen
# missing

from dataclasses import FrozenInstanceError, MISSING

e = FrozenInstanceError()
print("frozen")

print("missing")
