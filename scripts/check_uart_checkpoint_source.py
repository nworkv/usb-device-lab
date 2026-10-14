"""Read-only AST guard for the current UART checkpoint source contract, not semantics."""
import argparse
import ast
import json
from pathlib import Path


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = dotted(node.value)
        return parent + '.' + node.attr if parent else ''
    return ''


def writes(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and dotted(n.func) == 'checkpoint.write']


def advances(call):
    if len(call.args) != 5:
        return False
    index = call.args[2]
    return (isinstance(index, ast.BinOp) and isinstance(index.op, ast.Add)
        and dotted(index.left) == 'iteration' and isinstance(index.right, ast.Constant)
        and type(index.right.value) is int and index.right.value == 1
        and dotted(call.args[3]) == 'mutator.random')


def guarded(node):
    test = node.test
    return (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
        and isinstance(test.operand, ast.Call) and dotted(test.operand.func) == 'stop.is_set'
        and not test.operand.args and not test.operand.keywords)


def check(text):
    tree = ast.parse(text)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_campaign']
    if len(functions) != 1:
        return ['Exactly one top-level run_campaign function is required']
    function = functions[0]
    errors = []
    if not any(len(c.args) == 5 and dotted(c.args[2]) == 'first' and dotted(c.args[3]) == 'mutator.random' for c in writes(function)):
        errors.append('Initial checkpoint for first iteration/PRNG is missing')
    if not any(isinstance(n, ast.Call) and dotted(n.func) == 'mutator.random.setstate' for n in ast.walk(function)):
        errors.append('PRNG restoration call is missing')
    loops = [n for n in ast.walk(function) if isinstance(n, ast.For) and dotted(n.target) == 'iteration']
    if not any(isinstance(n, ast.If) and guarded(n) and any(advances(c) for statement in n.body for c in writes(statement))
               for loop in loops for n in loop.body):
        errors.append('Guarded checkpoint advancement after an iteration is missing')
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check current UART checkpoint AST source contract')
    parser.add_argument('--source', default=str(Path(__file__).resolve().parents[1] / 'usb_device_lab' / 'uart_campaign.py'))
    args = parser.parse_args(argv)
    try:
        path = Path(args.source)
        with path.open('r', encoding='utf-8') as stream:
            text = stream.read(262145)
        if len(text) > 262144:
            raise ValueError('Source exceeds 256 Ki characters')
        errors = check(text)
    except (OSError, SyntaxError, ValueError) as error:
        errors = [type(error).__name__ + ': ' + str(error)]
    print(json.dumps(dict(source=args.source, source_contract_passed=not errors, errors=errors,
                         runtime_verified=False), ensure_ascii=False))
    return 1 if errors else 0

if __name__ == '__main__':
    raise SystemExit(main())
