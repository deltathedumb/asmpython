# probes: __and__/__or__/__xor__ serve the bit operators
# expect:
# 2
# 7
# 5
class Flags:
    def __init__(self, bits):
        self.bits = bits

    def __and__(self, other):
        return Flags(self.bits & other.bits)

    def __or__(self, other):
        return Flags(self.bits | other.bits)

    def __xor__(self, other):
        return Flags(self.bits ^ other.bits)


print((Flags(6) & Flags(3)).bits)
print((Flags(6) | Flags(3)).bits)
print((Flags(6) ^ Flags(3)).bits)
