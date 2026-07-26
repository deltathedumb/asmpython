# expect:
# [0, 2] [0, 1]
docs = ['the cat', 'the dog', 'a cat']
index = {}
for doc_id, doc in enumerate(docs):
    for word in doc.split():
        index.setdefault(word, []).append(doc_id)
print(sorted(index.get('cat', [])), sorted(index.get('the', [])))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'append'
