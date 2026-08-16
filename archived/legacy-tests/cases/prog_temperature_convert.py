# expect:
# [32.0, 77.0, 212.0]
temps_c = [0, 25, 100]
temps_f = [c * 9 / 5 + 32 for c in temps_c]
print(temps_f)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)
