# expect:
# [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
def sieve(n):
    is_prime = [True] * (n + 1)
    is_prime[0] = is_prime[1] = False
    for i in range(2, int(n ** 0.5) + 1):
        if is_prime[i]:
            for j in range(i * i, n + 1, i):
                is_prime[j] = False
    return [i for i in range(n + 1) if is_prime[i]]
print(sieve(30))
# asmpython (beta/3.14.0) rejects at compile: [P002] expected NEWLINE, got OP '='
