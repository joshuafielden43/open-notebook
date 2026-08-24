from typing import Any

from loguru import logger

from open_notebook import logging_config


def test_safe_logging_disables_diagnose(monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(logging_config.logger, "remove", lambda: calls.append("remove"))
    monkeypatch.setattr(
        logging_config.logger,
        "add",
        lambda sink, **kwargs: calls.append((sink, kwargs)),
    )
    monkeypatch.setattr(logging_config, "_configured", False)

    logging_config.configure_safe_logging()

    assert calls[0] == "remove"
    assert calls[1][1]["diagnose"] is False
    assert calls[1][1]["backtrace"] is True
    assert logger is logging_config.logger
