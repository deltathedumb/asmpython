# expect:
# 8
from itertools import product
print(len(list(product([0, 1], repeat=3))))
# asmpython (beta/3.14.0) rejects at compile: [E021] product() got an unexpected keyword argument 'repeat'
