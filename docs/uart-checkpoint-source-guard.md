# UART checkpoint source-contract guard

```sh
python scripts/check_uart_checkpoint_source.py
python3 -m unittest discover -s tests -p 'test_uart_checkpoint_source.py' -v
```

Reads/parses Python AST only; does not import campaign code or open UART/USB.
Checks the current implementation's structural contract: one top-level
run_campaign, initial checkpoint with first/Mutator PRNG, setstate restoration,
and iteration+1 checkpoint write inside a top-level iteration-loop conditional
not stop.is_set(). The omitted progress block in b5cec965 is rejected by this
shape check; bc114cec restored that block.

CI runs the guard before installing/importing/executing the campaign and retains
source-contract.log with the existing diagnostics. Failure is nonzero, including
syntax/read errors; the JSON result always says runtime_verified=false.

This is deliberately narrow and tied to current names/structure, not a semantic
proof. Dead/unreachable code, misleading helpers or unrelated control-flow bugs
can satisfy its shape. A legitimate refactor can require updating the contract.
It does not replace checkpoint unit tests, PTY integration tests, real stand
acceptance or inspection of the actual pushed diff. It must never be described as
proof that resume works or a safe hardware-state check.

Six guard tests passed locally with positive/negative source fixtures, including
missing progress, missing init/restore, wrong counter/guard and malformed source.
Workflow YAML parsed locally. Full checkout source check, all UART tests and
GitHub Actions status were not verified in this preparation. Product code is
unchanged; this adds an early failure detector for the transfer regression.
