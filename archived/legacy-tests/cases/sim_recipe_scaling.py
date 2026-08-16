# expect:
# eggs: 3.0
# flour: 300.0
# sugar: 150.0
ingredients = {'flour': 200, 'sugar': 100, 'eggs': 2}
scale = 1.5
scaled = {k: v * scale for k, v in ingredients.items()}
for k in sorted(scaled):
    print(k + ': ' + str(scaled[k]))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr DictComprehension (float value)
