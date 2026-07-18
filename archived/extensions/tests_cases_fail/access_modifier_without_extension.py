# expect-error: requires the 'access' extension

class Account:
    @private
    def secret(self) -> int:
        return 1

a = Account()
print(a.secret())
