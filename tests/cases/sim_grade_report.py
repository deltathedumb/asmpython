# expect:
# alice: 89.0
# bob: 82.7
students = {'alice': [90, 85, 92], 'bob': [78, 82, 88]}
for name in sorted(students):
    grades = students[name]
    avg = sum(grades) / len(grades)
    print(name + ': ' + str(round(avg, 1)))
# asmpython (beta/3.14.0) MISMATCH: prints 'alice: 30.0\nbob: 26.0\n' (wrong).
