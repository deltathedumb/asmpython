# expect:
# 1
# 2
# 5
# 6
# 1
# 6
# 1
# 6
# 0
# 1

import fractions

f = fractions.Fraction(1, 2)
print(f.numerator)
print(f.denominator)

f2 = fractions.Fraction(1, 3)
f3 = f._add(f2)
print(f3.numerator)
print(f3.denominator)

f4 = f._sub(f2)
print(f4.numerator)
print(f4.denominator)

f5 = f._mul(f2)
print(f5.numerator)
print(f5.denominator)

f6 = fractions.Fraction(0, 5)
print(f6.numerator)
print(f6.denominator)
