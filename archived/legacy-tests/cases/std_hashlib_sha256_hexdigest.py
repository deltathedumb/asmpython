# probes: hashlib.sha256 accepts data in the constructor
# expect:
# ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
import hashlib

print(hashlib.sha256(b"abc").hexdigest())
