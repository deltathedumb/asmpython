# expect:
# foo world hello
def rev_words(s):
    words = s.split(' ')
    result = ''
    for i in range(len(words) - 1, -1, -1):
        result += words[i]
        if i > 0:
            result += ' '
    return result
print(rev_words('hello world foo'))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
