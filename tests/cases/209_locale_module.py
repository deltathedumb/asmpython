# expect:
# C
# $42.5

import locale

print(locale.setlocale(locale.LC_ALL, 'C'))
print(locale.currency(42.5, symbol=1))
