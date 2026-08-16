# expect:
# ['alice', 'carol']
students = [('math', 'alice'), ('cs', 'bob'), ('math', 'carol')]
by_subject = {}
for subject, name in students:
    by_subject.setdefault(subject, []).append(name)
print(sorted(by_subject.get('math', [])))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'append'
