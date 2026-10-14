"""Hand-written test fixture: call sites the AST reader must find (and some it must not)."""
open("EXECUTED_MARKER", "w").write("this file was executed")  # parsing must never run this

chain = object()

# a comment that mentions chain.compose_call( is not a call site
text = "chain.compose_call(call_module='Fake', call_function='fake')"

call = chain.compose_call(
    call_module='Tokens',
    call_function='issue',
    call_params={'to': 'x', 'amount': 1},
)

positional = chain.compose_call('Tokens', 'lock', {'who': 'x'})


def send(keep_alive=True):
    func = ('transfer_keep_alive' if keep_alive
            else 'transfer')
    return chain.compose_call(call_module='Tokens', call_function=func, call_params=build())


def by_parameter(func):
    return chain.compose_call(call_module='Tokens', call_function=func, call_params={})


def shadowed():
    func = 'burn'

    def inner():
        func = compute()
        return chain.compose_call(call_module='Tokens', call_function=func, call_params={})
    return inner, chain.compose_call(call_module='Tokens', call_function=func, call_params={})
