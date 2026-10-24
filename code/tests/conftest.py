from __future__ import annotations

import sys
from pathlib import Path

import pytest

CODE_DIR = Path(__file__).resolve().parents[1]
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from config import DATASET_DIR  # noqa: E402
from graph.identity import IdentityResolver  # noqa: E402
from ingest.loader import load_graph  # noqa: E402


@pytest.fixture(scope="session")
def graph():
    loaded, _ = load_graph(DATASET_DIR)
    return loaded


@pytest.fixture(scope="session")
def resolver():
    return IdentityResolver(DATASET_DIR)
