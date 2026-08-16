# expect:
# 4 19
def metrics(text):
    words = text.split()
    return {'words': len(words), 'chars': len(text), 'avg_word': sum(len(w) for w in words) / len(words)}
m = metrics('the quick brown fox')
print(m['words'], m['chars'])
# asmpython (beta/3.14.0) rejects at compile: [E148] mixed dict value types (int and float); a float value can't share a dict with non-floats
