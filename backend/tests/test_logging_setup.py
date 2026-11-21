"""A copy of the log in the data volume: the one of Docker goes away with the container on every deploy (09-11-2026)."""
from __future__ import annotations

import logging

import pytest

from app import logging_setup


@pytest.fixture
def root_handlers():
    root = logging.getLogger()
    before = list(root.handlers)
    yield root
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def evbro_files(root):
    return [h for h in root.handlers if getattr(h, "_evbro_log_file", None)]


def test_the_log_goes_to_a_rotating_file_in_the_data_folder(tmp_path, root_handlers) -> None:
    path = logging_setup.install_file_logging(str(tmp_path))
    assert path == str(tmp_path / "logs" / "evbro.log")
    logging.getLogger("app.services.poller").warning("Frozen position on 000001: test line")
    for handler in evbro_files(root_handlers):
        handler.flush()
    text = (tmp_path / "logs" / "evbro.log").read_text(encoding="utf-8")
    assert "[WARNING] app.services.poller: Frozen position on 000001: test line" in text
    (handler,) = evbro_files(root_handlers)
    assert handler.maxBytes == logging_setup.LOG_FILE_MAX_BYTES and handler.backupCount == logging_setup.LOG_FILE_BACKUPS


def test_installing_twice_keeps_one_file_and_another_folder_replaces_it(tmp_path, root_handlers) -> None:
    logging_setup.install_file_logging(str(tmp_path / "a"))
    logging_setup.install_file_logging(str(tmp_path / "a"))
    assert len(evbro_files(root_handlers)) == 1
    logging_setup.install_file_logging(str(tmp_path / "b"))
    (handler,) = evbro_files(root_handlers)
    assert handler._evbro_log_file == str(tmp_path / "b" / "logs" / "evbro.log")


def test_a_folder_that_cannot_be_written_leaves_the_console_log_alone(tmp_path, root_handlers) -> None:
    blocked = tmp_path / "blocked"
    blocked.write_text("a file where the folder should be")
    assert logging_setup.install_file_logging(str(blocked)) is None
    assert evbro_files(root_handlers) == []
