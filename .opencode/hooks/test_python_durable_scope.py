"""Python CLI argument data must not supply this literal-code fact's write call."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hook_facts as H


DATA = '"open(\'CLAUDE.md\',\'w\')"'
WRITE = '"open(\'CLAUDE.md\',\'w\').write(\'fixture\')"'


@pytest.mark.parametrize('prefix', [
    'python -m audit_reader ', 'python audit_reader.py ', 'python -- audit_reader.py ',
    'python -W ignore -X dev -m audit_reader ', 'py -3 -m audit_reader ',
    'python -Bm audit_reader ', 'env FLAG=x nice -n 5 python -m audit_reader ',
    'python -c "print(1)" ', 'python -cprint(1) ',
    'python -c "print(1)" -c ',
])
def test_argument_data_is_not_a_literal_python_write(prefix):
    assert H.facts({'tool_input': {'command': prefix + DATA}}, {})['durable_python_open_w'] is False


@pytest.mark.parametrize('command', [
    'python -c ' + WRITE, 'python -Bc ' + WRITE, 'python -c' + WRITE,
    'python -c "import sys; open(sys.argv[1],\'w\')" CLAUDE.md',
    'PY=python; $PY -c ' + WRITE,
    'FLAGS=-c; python $FLAGS ' + WRITE,
    'P=CLAUDE.md; python -c "open(\'$P\',\'w\')"',
    'MODE=w; python -c "open(\'CLAUDE.md\',\'$MODE\')"',
    'exec nice -n 5 python -c ' + WRITE,
    'bash <<\'EOF\'\npython -c ' + WRITE + '\nEOF',
    'python -m timeit ' + WRITE, 'python -i audit_reader.py ' + DATA,
    'python -m pdb -c "p open(\'CLAUDE.md\',\'w\')" audit_reader.py',
    'python -c "import sys; exec(sys.argv[1])" ' + DATA,
    'python -c "import sys; eval(sys.argv[1])" ' + DATA,
    'python -c "import sys; f=exec; f(sys.argv[1])" ' + DATA,
    'python -c "print(eval(sys.argv[1]))" ' + DATA,
    'python -c "print(*[exec(sys.argv[1])])" ' + DATA,
    'python -c "import audit_helper; audit_helper.run()" ' + DATA,
    'uv run python -c ' + WRITE, 'python --unknown-option ' + DATA,
])
def test_real_code_and_conservative_unknown_carriers_keep_the_fact(command):
    assert H.facts({'tool_input': {'command': command}}, {})['durable_python_open_w'] is True
