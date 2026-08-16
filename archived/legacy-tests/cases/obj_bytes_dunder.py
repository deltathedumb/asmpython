# probes: __bytes__ serves bytes()
# expect:
# b'payload'
class Blob:
    def __bytes__(self):
        return b"payload"


print(bytes(Blob()))
