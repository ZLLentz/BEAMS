import logging
import os
import sys
from contextlib import contextmanager
from copy import copy

import py_trees.logging
import pytest

from beams.logging import setup_logging


@pytest.fixture(autouse=True)
def central_logging_setup(caplog):
    # Set pytest debugging level, and capture that output
    # Without this logging calls made after the test may fire after the listener
    # thread is closed.
    caplog.set_level(logging.DEBUG)
    # set debug level in case people are curious
    setup_logging(logging.DEBUG)
    # Set py_trees logging level (not our library)
    py_trees.logging.level = py_trees.logging.Level.DEBUG


@pytest.fixture(autouse=True)
def ca_env_vars():
    # Pick a non-standard port to avoid collisions with same-named prod PVs
    os.environ["EPICS_CA_SERVER_PORT"] = "5066"
    # Only broadcast and get on local if
    os.environ["EPICS_CA_AUTO_ADDR_LIST"] = "NO"
    os.environ["EPICS_CA_ADDR_LIST"] = "localhost"


@contextmanager
def cli_args(args):
    """
    Context manager for running a block of code with a specific set of
    command-line arguments.
    """
    prev_args = sys.argv
    sys.argv = args
    yield
    sys.argv = prev_args


@contextmanager
def restore_logging():
    """
    Context manager for reverting our logging config after testing a function
    that configures the logging.
    """
    prev_handlers = copy(logging.root.handlers)
    yield
    logging.root.handlers = prev_handlers
