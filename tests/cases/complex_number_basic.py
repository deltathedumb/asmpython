# expect:
# 3.0 4.0 5.0
z = complex(3, 4)
print(z.real, z.imag, abs(z))
# asmpython (beta/3.14.0) rejects at compile: [E002] undefined function 'complex'
