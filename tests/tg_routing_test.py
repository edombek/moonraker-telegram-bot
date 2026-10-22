import re

from tg.base import (
    CallbackRegistration,
    CommandRegistration,
    HandlerRegistry,
    TgUpdate,
    resolve_callback_handler,
    resolve_message_handler,
)


async def unknown_chat(_update: TgUpdate) -> None:
    return None


async def status(_update: TgUpdate) -> None:
    return None


async def help_command(_update: TgUpdate) -> None:
    return None


async def macros(_update: TgUpdate) -> None:
    return None


async def upload_file(_update: TgUpdate) -> None:
    return None


async def echo_unknown(_update: TgUpdate) -> None:
    return None


async def lapse(_update: TgUpdate) -> None:
    return None


async def gcode(_update: TgUpdate) -> None:
    return None


async def catch_all_callback(_update: TgUpdate) -> None:
    return None


def make_registry() -> HandlerRegistry:
    registry = HandlerRegistry(unknown_chat=unknown_chat)
    registry.commands = [
        CommandRegistration("status", status),
        CommandRegistration("help", help_command, parallel=True),
    ]
    registry.callbacks = [
        CallbackRegistration(re.compile("lapse:"), lapse),
        CallbackRegistration(re.compile(r"^\S[^\:]+\.gcode$"), gcode),
        CallbackRegistration(None, catch_all_callback),
    ]
    registry.catch_all_command = macros
    registry.document = upload_file
    registry.unknown_text = echo_unknown
    return registry


def test_exact_command_match() -> None:
    assert resolve_message_handler(make_registry(), "/status") is status


def test_command_with_botname_and_args() -> None:
    assert resolve_message_handler(make_registry(), "/status@MyBot arg1") is status


def test_command_case_insensitive() -> None:
    assert resolve_message_handler(make_registry(), "/HELP") is help_command


def test_unknown_command_falls_to_catch_all() -> None:
    assert resolve_message_handler(make_registry(), "/somesmacro") is macros


def test_plain_text_goes_to_unknown_text() -> None:
    assert resolve_message_handler(make_registry(), "hello") is echo_unknown


def test_no_text_goes_to_document() -> None:
    assert resolve_message_handler(make_registry(), None) is upload_file
    assert resolve_message_handler(make_registry(), "") is upload_file


def test_callback_first_pattern_wins() -> None:
    registry = make_registry()
    assert resolve_callback_handler(registry, "lapse:123") is lapse
    assert resolve_callback_handler(registry, "somefile.gcode") is gcode


def test_callback_falls_to_catch_all() -> None:
    assert resolve_callback_handler(make_registry(), "whatever") is catch_all_callback


def test_callback_without_match_returns_none() -> None:
    registry = HandlerRegistry(unknown_chat=unknown_chat)
    registry.callbacks = [CallbackRegistration(re.compile("lapse:"), lapse)]
    assert resolve_callback_handler(registry, "whatever") is None
    assert resolve_callback_handler(registry, None) is None
