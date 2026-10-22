"""Telegram transport backends: Bot API over HTTPS (default) and MTProto via Telethon."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from configuration import ConfigWrapper
    from tg.base import TelegramBackend


def create_backend(config: ConfigWrapper) -> TelegramBackend:
    """Instantiate the backend selected by ``[bot] api_type``."""
    if config.bot_config.api_type == "mtproto":
        from tg.mtproto import MTProtoBackend  # noqa: PLC0415

        return MTProtoBackend(config)

    from tg.bot_api import BotApiBackend  # noqa: PLC0415

    return BotApiBackend(config)
