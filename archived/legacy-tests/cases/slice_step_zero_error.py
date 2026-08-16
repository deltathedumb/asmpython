# expect:
# step zero error
try:
    x = [1, 2, 3][::0]
except ValueError:
    print('step zero error')
# asmpython (beta/3.14.0) runtime failure: exe HANG (>10s, likely infinite loop)
