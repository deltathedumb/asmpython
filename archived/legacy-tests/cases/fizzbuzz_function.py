# expect:
# ['1', '2', 'Fizz', '4', 'Buzz']
def fizzbuzz(n):
    result = []
    for i in range(1, n + 1):
        s = ''
        if i % 3 == 0:
            s += 'Fizz'
        if i % 5 == 0:
            s += 'Buzz'
        result.append(s or str(i))
    return result
print(fizzbuzz(5))
# asmpython (beta/3.14.0) MISMATCH: prints '[5368737792, 5368737792, 9343936, 5368737792, 9343968]\n' (wrong).
