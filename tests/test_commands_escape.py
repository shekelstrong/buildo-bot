"""Tests for commands_escape router (global /start, /cancel, menu escape)."""

from __future__ import annotations

import os

os.environ.setdefault(
    "TELEGRAM_BOT_TOKEN", "1234567890:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
)
os.environ.setdefault("ADMIN_TELEGRAM_ID", "6318513424")


def test_commands_escape_module_imports():
    from bot.handlers import commands_escape

    assert hasattr(commands_escape, "router")
    assert commands_escape.router.name == "commands-escape"


def test_commands_escape_handles_start():
    """Router should have /start handler in source code."""
    import inspect
    from bot.handlers import commands_escape

    src = inspect.getsource(commands_escape)
    assert "CommandStart" in src
    assert "/start" in src or "Сбросил" in src


def test_commands_escape_handles_cancel():
    import inspect
    from bot.handlers import commands_escape

    src = inspect.getsource(commands_escape)
    assert 'Command("cancel")' in src


def test_commands_escape_handles_text_triggers():
    """'в меню', 'отмена' и т.п. — текстовые триггеры."""
    import inspect
    from bot.handlers import commands_escape

    src = inspect.getsource(commands_escape)
    assert "в меню" in src
    assert "отмена" in src


def test_commands_escape_registered_first():
    """commands_escape должен быть зарегистрирован в main.py и в начале."""
    import bot.main as bot_main

    assert hasattr(
        bot_main, "commands_escape_handlers"
    ), "main.py должен импортировать commands_escape_handlers"
    assert hasattr(
        bot_main, "auth_github_handlers"
    ), "main.py должен импортировать auth_github_handlers"


def test_global_escape_clears_state():
    """global_escape должен вызывать state.clear()."""
    import inspect
    from bot.handlers.commands_escape import global_escape

    src = inspect.getsource(global_escape)
    assert "state.clear" in src, "global_escape must call state.clear()"
    assert "Сбросил состояние" in src or "сброс" in src.lower()
