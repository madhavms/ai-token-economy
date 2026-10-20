# Shop

A tiny shopping cart used to measure how a coding agent spends tokens.

## Layout
- `shop/cart.py`: the cart: `total()` and `with_vat()`. Prices are whole pence.
- `tests/test_cart.py`: unit tests.

## Rules
- Run the tests with `python3 -m unittest -q` from the repo root.
- Keep changes small and targeted; don't rewrite whole files.
- Prices stay integers (pence); never use floats for money outside `with_vat()`.
- Reply in one or two sentences when the task is done.
