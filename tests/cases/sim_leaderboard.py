# expect:
# 1. p2 (2300)
# 2. p3 (1800)
# 3. p1 (1500)
scores = {'p1': 1500, 'p2': 2300, 'p3': 1800}
ranked = sorted(scores.items(), key=lambda x: -x[1])
for i, (player, score) in enumerate(ranked):
    print(str(i + 1) + '. ' + player + ' (' + str(score) + ')')
# asmpython (beta/3.14.0) rejects at compile: [E116] for ... in enumerate(...) needs two targets (`for i, x in enumerate(xs)`)
