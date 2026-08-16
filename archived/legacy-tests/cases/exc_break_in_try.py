# expect:
# f 0
# f 1
# f 2
# done
for i in range(5):
    try:
        if i == 2:
            break
    finally:
        print('f', i)
print('done')
# asmpython (beta/3.14.0) MISMATCH: prints 'f 0\nf 1\ndone\n' (wrong).
