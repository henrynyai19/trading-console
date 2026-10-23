import pytest

from app.config import Settings
from app.runtime import build_runtime, set_runtime


@pytest.fixture(autouse=True)
def _unconfigured_runtime():
    set_runtime(build_runtime(Settings()))
    yield
    set_runtime(None)
