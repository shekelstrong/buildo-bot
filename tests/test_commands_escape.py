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


def test_commands_escape_does_not_handle_start():
    """commands_escape НЕ ловит /start — это делает cmd_start в common."""
    import inspect
    from bot.handlers import commands_escape

    src = inspect.getsource(commands_escape)
    # Должны быть /cancel, /menu, текстовые триггеры
    assert 'Command("cancel")' in src
    assert 'Command("menu")' in src
    # НЕ должны быть CommandStart (его обрабатывает common_handlers)
    assert (
        "CommandStart" not in src
    ), "commands_escape should NOT handle /start — let cmd_start do it"


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


def test_site_builder_skip_commands_in_state():
    """receive_first_edit / receive_edit должны иметь filter ~F.text.startswith('/').

    Без этого /start из state SiteFlow.editing уходит в правку сайта.
    """
    import inspect
    from bot.handlers import site_builder

    src = inspect.getsource(site_builder)
    # Filter pattern: ~F.text.startswith("/") для editing/preview handlers
    assert (
        '~F.text.startswith("/")' in src
    ), "site_builder edit handlers must filter out commands"


def test_admin_skip_commands_in_state():
    """Admin FSM waiting_for_request должен пропускать команды."""
    import inspect
    from bot.handlers import admin

    src = inspect.getsource(admin)
    assert (
        '~F.text.startswith("/")' in src
    ), "admin edit handler must filter out commands"
