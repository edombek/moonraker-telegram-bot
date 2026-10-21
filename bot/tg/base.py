"""Backend-agnostic Telegram API types shared by the Bot API and MTProto backends."""

from __future__ import annotations

import abc
from collections.abc import Awaitable
import dataclasses
import enum
import json
import re
from typing import IO, Any, Callable, ClassVar, Union

_MEDIA_WRITE_TIMEOUT: int = 120


class ChatAction(enum.Enum):
    """Chat actions signalling an ongoing operation to chat members."""

    TYPING = "typing"
    UPLOAD_PHOTO = "upload_photo"
    RECORD_VIDEO = "record_video"
    UPLOAD_DOCUMENT = "upload_document"


class ParseMode:
    """Parse mode constants matching Bot API string values."""

    HTML: ClassVar[str] = "HTML"
    MARKDOWN_V2: ClassVar[str] = "MarkdownV2"


class TgApiError(Exception):
    """Telegram API error reported by a backend (Bot API BadRequest equivalent)."""


class InputMediaKind(enum.Enum):
    """Media types usable in media groups."""

    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"


@dataclasses.dataclass(frozen=True)
class TgMessageEntity:
    """Text formatting entity with Bot API style utf-16 offsets."""

    type: str
    offset: int
    length: int


@dataclasses.dataclass(frozen=True)
class InlineKeyboardButton:
    """Inline keyboard button carrying a callback payload."""

    text: str
    callback_data: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "callback_data": self.callback_data}


@dataclasses.dataclass(frozen=True)
class InlineKeyboardMarkup:
    """Inline keyboard: rows of callback buttons."""

    inline_keyboard: list[list[InlineKeyboardButton]]

    def to_dict(self) -> dict[str, Any]:
        return {"inline_keyboard": [[button.to_dict() for button in row] for row in self.inline_keyboard]}


@dataclasses.dataclass(frozen=True)
class ReplyKeyboardMarkup:
    """Regular reply keyboard with plain text buttons."""

    keyboard: list[list[str]]
    resize_keyboard: bool = True


@dataclasses.dataclass(frozen=True)
class ReplyKeyboardRemove:
    """Instruction to hide the reply keyboard."""


@dataclasses.dataclass(frozen=True)
class TgBotCommand:
    """Entry of the bot command menu."""

    command: str
    description: str


@dataclasses.dataclass(frozen=True)
class TgInputMedia:
    """Media item for send/reply media group calls."""

    kind: InputMediaKind
    media: bytes
    filename: str | None = None
    caption: str = ""
    parse_mode: str | None = None


class TgDocument(abc.ABC):
    """Incoming document attachment."""

    def __init__(self, file_name: str | None) -> None:
        self.file_name = file_name

    @abc.abstractmethod
    async def download(self) -> bytes:
        """Download and return the document contents."""


class TgMessage(abc.ABC):
    """Handle to a sent or incoming message usable to inspect or modify it."""

    def __init__(self, chat_id: int, message_id: int) -> None:
        self.chat_id = chat_id
        self.message_id = message_id

    @property
    @abc.abstractmethod
    def text(self) -> str | None: ...

    @property
    @abc.abstractmethod
    def caption(self) -> str | None: ...

    @property
    @abc.abstractmethod
    def entities(self) -> list[TgMessageEntity]: ...

    @property
    @abc.abstractmethod
    def caption_entities(self) -> list[TgMessageEntity]: ...

    @property
    @abc.abstractmethod
    def document(self) -> TgDocument | None: ...

    @property
    @abc.abstractmethod
    def reply_markup(self) -> InlineKeyboardMarkup | None: ...

    @abc.abstractmethod
    async def get_reply_message(self) -> TgMessage | None:
        """Return the message this one replies to, if any."""

    @property
    @abc.abstractmethod
    def sender_is_bot(self) -> bool:
        """Whether the message was sent by this bot itself."""

    @abc.abstractmethod
    def extract_entity_text(self, entity: TgMessageEntity, caption: bool = False) -> str:
        """Return the text covered by the given formatting entity."""

    def to_json(self) -> str:
        """Best-effort serializable representation for logging."""
        payload = {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "text": self.text,
            "caption": self.caption,
            "document": self.document.file_name if self.document else None,
            "reply_markup": self.reply_markup.to_dict() if self.reply_markup else None,
        }
        dumped = json.dumps(payload, default=str)
        return dumped.decode() if isinstance(dumped, bytes) else dumped

    @abc.abstractmethod
    async def send_chat_action(self, action: ChatAction) -> None: ...

    @abc.abstractmethod
    async def reply_text(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        reply_markup: ReplyMarkup = None,
        do_quote: bool = False,
    ) -> TgMessage: ...

    @abc.abstractmethod
    async def reply_photo(
        self,
        photo: bytes | IO[bytes],
        *,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: ReplyMarkup = None,
        disable_notification: bool = False,
        do_quote: bool = False,
        caption_entities: list[TgMessageEntity] | None = None,
    ) -> TgMessage: ...

    @abc.abstractmethod
    async def reply_video(
        self,
        video: bytes | IO[bytes],
        *,
        thumbnail: bytes | IO[bytes] | None = None,
        width: int = 0,
        height: int = 0,
        caption: str = "",
        parse_mode: str | None = None,
        disable_notification: bool = False,
        do_quote: bool = False,
    ) -> TgMessage: ...

    @abc.abstractmethod
    async def reply_media_group(self, media: list[TgInputMedia], *, disable_notification: bool = False, do_quote: bool = False) -> list[TgMessage]: ...

    @abc.abstractmethod
    async def edit_text(self, text: str, *, parse_mode: str | None = None, reply_markup: ReplyMarkup = None) -> None: ...

    @abc.abstractmethod
    async def edit_caption(self, caption: str, *, parse_mode: str | None = None, reply_markup: ReplyMarkup = None) -> None: ...

    @abc.abstractmethod
    async def edit_media(self, photo: bytes | IO[bytes]) -> None: ...

    @abc.abstractmethod
    async def delete(self) -> None: ...


class TgCallbackQuery(abc.ABC):
    """Inline keyboard button press."""

    def __init__(self, message: TgMessage | None, data: str | None) -> None:
        self.message = message
        self.data = data

    @abc.abstractmethod
    async def answer(self, text: str | None = None, show_alert: bool = False) -> None: ...

    @abc.abstractmethod
    async def edit_message_text(self, text: str, *, parse_mode: str | None = None, reply_markup: ReplyMarkup = None) -> None: ...

    @abc.abstractmethod
    async def delete_message(self) -> None: ...


@dataclasses.dataclass(frozen=True)
class TgUpdate:
    """Neutral incoming update passed to every handler."""

    effective_message: TgMessage | None
    callback_query: TgCallbackQuery | None = None
    effective_chat_id: int | None = None
    chat_is_group: bool = False
    chat_username: str | None = None


Handler = Callable[[TgUpdate], Awaitable[None]]
ReplyMarkup = Union[InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, None]


@dataclasses.dataclass(frozen=True)
class CommandRegistration:
    """Command handler registration."""

    name: str
    handler: Handler
    parallel: bool = False


@dataclasses.dataclass(frozen=True)
class CallbackRegistration:
    """Callback query handler registration; ``pattern=None`` is a catch-all."""

    pattern: re.Pattern[str] | None
    handler: Handler


@dataclasses.dataclass
class HandlerRegistry:
    """Declarative table of handlers consumed by every backend."""

    unknown_chat: Handler
    commands: list[CommandRegistration] = dataclasses.field(default_factory=list)
    callbacks: list[CallbackRegistration] = dataclasses.field(default_factory=list)
    catch_all_command: Handler | None = None
    document: Handler | None = None
    unknown_text: Handler | None = None


_COMMAND_RE = re.compile(r"^/([A-Za-z0-9_]{1,32})(?:@\w+)?(?:\s|$)")


def resolve_message_handler(registry: HandlerRegistry, text: str | None) -> Handler | None:
    """Pick the handler for an incoming message the way the Bot API dispatch does."""
    if text:
        match = _COMMAND_RE.match(text)
        if match:
            name = match.group(1).lower()
            for registration in registry.commands:
                if registration.name == name:
                    return registration.handler
            return registry.catch_all_command
        return registry.unknown_text
    return registry.document


def resolve_callback_handler(registry: HandlerRegistry, data: str | None) -> Handler | None:
    """Pick the first callback handler whose pattern matches, like CallbackQueryHandler groups do."""
    for registration in registry.callbacks:
        if registration.pattern is None or (data is not None and registration.pattern.search(data)):
            return registration.handler
    return None


class TelegramBackend(abc.ABC):
    """Transport-level Telegram operations used by the whole bot."""

    @abc.abstractmethod
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: ReplyMarkup = None,
        disable_notification: bool = False,
        message_thread_id: int | None = None,
    ) -> TgMessage: ...

    @abc.abstractmethod
    async def send_photo(
        self,
        chat_id: int,
        photo: bytes | IO[bytes],
        *,
        caption: str = "",
        parse_mode: str | None = None,
        reply_markup: ReplyMarkup = None,
        disable_notification: bool = False,
        message_thread_id: int | None = None,
        caption_entities: list[TgMessageEntity] | None = None,
    ) -> TgMessage: ...

    @abc.abstractmethod
    async def send_video(
        self,
        chat_id: int,
        video: bytes | IO[bytes],
        *,
        thumbnail: bytes | IO[bytes] | None = None,
        width: int = 0,
        height: int = 0,
        caption: str = "",
        parse_mode: str | None = None,
        disable_notification: bool = False,
        filename: str | None = None,
    ) -> TgMessage: ...

    @abc.abstractmethod
    async def send_media_group(self, chat_id: int, media: list[TgInputMedia], *, disable_notification: bool = False) -> list[TgMessage]: ...

    @abc.abstractmethod
    async def send_chat_action(self, chat_id: int, action: ChatAction, message_thread_id: int | None = None) -> None: ...

    @abc.abstractmethod
    async def delete_message(self, chat_id: int, message_id: int) -> None: ...

    @abc.abstractmethod
    async def pin_chat_message(self, chat_id: int, message_id: int, disable_notification: bool = False) -> None: ...

    @abc.abstractmethod
    async def unpin_chat_message(self, chat_id: int, message_id: int) -> None: ...

    @abc.abstractmethod
    async def unpin_all_chat_messages(self, chat_id: int) -> None: ...

    @abc.abstractmethod
    async def set_my_commands(self, commands: list[TgBotCommand]) -> None: ...

    @abc.abstractmethod
    def setup(self, registry: HandlerRegistry) -> None:
        """Subscribe the handler registry to incoming updates."""

    @abc.abstractmethod
    def run(self, ready_callback: Callable[[], None]) -> None:
        """Process updates until the backend stops (blocking)."""

    @property
    @abc.abstractmethod
    def bot_id(self) -> int:
        """Numeric id of this bot account."""
