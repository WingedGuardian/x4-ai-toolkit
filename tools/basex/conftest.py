"""BaseX's separately launched pytest suite shares the toolkit's safe temp lifecycle."""
from pathlib import Path
import sys
import pytest

sys.dont_write_bytecode = True
TOOLKIT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLKIT / "scripts"))
import x4pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    x4pytest.configure(config, TOOLKIT)


def pytest_sessionstart(session):
    x4pytest.session_start(session, TOOLKIT)
