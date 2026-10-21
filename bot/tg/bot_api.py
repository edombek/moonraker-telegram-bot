"""Backend implementation on top of python-telegram-bot (Bot API over HTTPS)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable
import urllib.parse

import telegram
from telegram import (
    BotCommand,
    InlineKeyboardMarkup as PtbInlineKeyboardMarkup,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    MessageEntity,
    ReplyKeyboardMarkup as PtbReplyKeyboardMarkup,
    ReplyKeyboardRemove as PtbReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatAction as PtbChatAction
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from tg.base import (
    _MEDIA_WRITE_TIMEOUT,
    ChatAction,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaKind,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    TelegramBackend,
    TgApiError,
    TgCallbackQuery,
    TgDocument,
    TgMessage,
    TgMessageEntity,
    TgUpdate,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine, Sequence

    from configuration import ConfigWrapper
    from tg.base import Handler, HandlerRegistry, TgBotCommand, TgInputMedia

logger = logging.getLogger(__name__)

_PTB_CHAT_ACTIONS = {
    ChatAction.TYPING: PtbChatAction.TYPING,
    ChatAction.UPLOAD_PHOTO: PtbChatAction.UPLOAD_PHOTO,
    ChatAction.RECORD_VIDEO: PtbChatAction.RECORD_VIDEO,
    ChatAction.UPLOAD_DOCUMENT: PtbChatAction.UPLOAD_DOCUMENT,
}

_PTB_MEDIA_CLASSES = {
    InputMediaKind.PHOTO: InputMediaPhoto,
    InputMediaKind.VIDEO: InputMediaVideo,
    InputMediaKind.DOCUMENT: InputMediaDocument,
}


def _to_ptb_markup(markup: Any) -> Any:
    if markup is None:
        return None
    if isinstance(markup, ReplyKeyboardRemove):
        return PtbReplyKeyboardRemove()
    if isinstance(markup, ReplyKeyboardMarkup):
        return PtbReplyKeyboardMarkup(markup.keyboard, resize_keyboard=markup.resize_keyboard)
    if isinstance(markup, InlineKeyboardMarkup):
        return PtbInlineKeyboardMarkup([[telegram.InlineKeyboardButton(button.text, callback_data=button.callback_data) for button in row] for row in markup.inline_keyboard])
    if isinstance(markup, PtbInlineKeyboardMarkup):
        return markup
    msg = f"Unsupported reply markup: {markup!r}"
    raise TypeError(msg)
    return None


def _neutralize_markup(markup: Any) -> InlineKeyboardMarkup | None:
    if isinstance(markup, PtbInlineKeyboardMarkup):
        rows = [[InlineKeyboardButton(text=button.text, callback_data=button.callback_data if isinstance(button.callback_data, str) else None) for button in row] for row in markup.inline_keyboard]
        return InlineKeyboardMarkup(rows)
    return None


def _neutral_entities(entities: Sequence[telegram.MessageEntity] | None) -> list[TgMessageEntity]:
    return [TgMessageEntity(entity.type, entity.offset, entity.length) for entity in entities or []]


def _ptb_input_media(item: TgInputMedia) -> Any:
    media_class = _PTB_MEDIA_CLASSES[item.kind]
    kwargs: dict[str, Any] = {"media": item.media}
    if item.filename:
        kwargs["filename"] = item.filename
    if item.caption:
        kwargs["caption"] = item.caption
        kwargs["parse_mode"] = item.parse_mode
    return media_class(**kwargs)


class _PTBDocument(TgDocument):
    def __init__(self, document: telegram.Document) -> None:
        super().__init__(document.file_name)
        self._document = document

    async def download(self) -> bytes:
        return bytes(await (await self._document.get_file()).download_as_bytearray())


class _PTBMessage(TgMessage):
    def __init__(self, backend: BotApiBackend, message: telegram.Message) -> None:
        super().__init__(message.chat_id, message.message_id)
        self._backend = backend
        self._message = message

    @property
    def text(self) -> str | None:
        return self._message.text

    @property
    def caption(self) -> str | None:
        return self._message.caption

    @property
    def entities(self) -> list[TgMessageEntity]:
        return _neutral_entities(self._message.entities)

    @property
    def caption_entities(self) -> list[TgMessageEntity]:
        return _neutral_entities(self._message.caption_entities)

    @property
    def document(self) -> TgDocument | None:
        if self._message.document is None:
            return None
        return _PTBDocument(self._message.document)

    @property
    def reply_markup(self) -> InlineKeyboardMarkup | None:
        return _neutralize_markup(self._message.reply_markup)

    async def get_reply_message(self) -> TgMessage | None:
        if self._message.reply_to_message is None:
            return None
        return _PTBMessage(self._backend, self._message.reply_to_message)

    @property
    def sender_is_bot(self) -> bool:
        return self._message.from_user is not None and self._message.from_user.id == self._backend.bot_id

    def extract_entity_text(self, entity: TgMessageEntity, caption: bool = False) -> str:
        source = self._message.caption_entities if caption else self._message.entities
        for ptb_entity in source or []:
            if ptb_entity.type == entity.type and ptb_entity.offset == entity.offset and ptb_entity.length == entity.length:
                if caption:
                    return str(self._message.parse_caption_entity(ptb_entity).strip())
                return str(self._message.parse_entity(ptb_entity).strip())
        source_text = self._message.caption if caption else self._message.text
        if source_text is None:
            return ""
        return source_text[entity.offset : entity.offset + entity.length].strip()

    async def send_chat_action(self, action: ChatAction) -> None:
        await self._backend.send_chat_action(self.chat_id, action)

    async def reply_text(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        disable_notification: bool = False,
        reply_markup: Any = None,
        do_quote: bool = False,
    ) -> TgMessage:
        return _PTBMessage(
            self._backend,
            await self._message.reply_text(text, parse_mode=parse_mode, disable_notification=disable_notification, reply_markup=_to_ptb_markup(reply_markup), do_quote=do_quote),
        )

    async def reply_photo(
        self,
        photo: Any,
        *,
        caption: str | None = None,
        parse_mode: str | None = None,
        reply_markup: Any = None,
        disable_notification: bool = False,
        do_quote: bool = False,
        caption_entities: list[TgMessageEntity] | None = None,
    ) -> TgMessage:
        return _PTBMessage(
            self._backend,
            await self._message.reply_photo(
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=_to_ptb_markup(reply_markup),
                disable_notification=disable_notification,
                do_quote=do_quote,
                caption_entities=[MessageEntity(entity.type, entity.offset, entity.length) for entity in caption_entities] if caption_entities else None,
            ),
        )

    async def reply_video(
        self,
        video: Any,
        *,
        thumbnail: Any = None,
        width: int = 0,
        height: int = 0,
        caption: str = "",
        parse_mode: str | None = None,
        disable_notification: bool = False,
        do_quote: bool = False,
    ) -> TgMessage:
        return _PTBMessage(
            self._backend,
            await self._message.reply_video(
                video=video,
                thumbnail=thumbnail,
                width=width or None,
                height=height or None,
                caption=caption,
                parse_mode=parse_mode,
                disable_notification=disable_notification,
                do_quote=do_quote,
                write_timeout=_MEDIA_WRITE_TIMEOUT,
            ),
        )

    async def reply_media_group(self, media: list[TgInputMedia], *, disable_notification: bool = False, do_quote: bool = False) -> list[TgMessage]:
        messages = await self._message.reply_media_group(
            [_ptb_input_media(item) for item in media],
            disable_notification=disable_notification,
            do_quote=do_quote,
            write_timeout=_MEDIA_WRITE_TIMEOUT,
        )
        return [_PTBMessage(self._backend, message) for message in messages]

    async def edit_text(self, text: str, *, parse_mode: str | None = None, reply_markup: Any = None) -> None:
        await self._message.edit_text(text, parse_mode=parse_mode, reply_markup=_to_ptb_markup(reply_markup))

    async def edit_caption(self, caption: str, *, parse_mode: str | None = None, reply_markup: Any = None) -> None:
        await self._message.edit_caption(caption, parse_mode=parse_mode, reply_markup=_to_ptb_markup(reply_markup))

    async def edit_media(self, photo: Any) -> None:
        await self._message.edit_media(InputMediaPhoto(photo))

    async def delete(self) -> None:
        await self._message.delete()


class _PTBCallbackQuery(TgCallbackQuery):
    def __init__(self, backend: BotApiBackend, query: telegram.CallbackQuery) -> None:
        message: TgMessage | None = None
        if isinstance(query.message, telegram.Message) and query.message.is_accessible:
            message = _PTBMessage(backend, query.message)
        super().__init__(message, query.data)
        self._query = query

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        await self._query.answer(text=text, show_alert=show_alert)

    async def edit_message_text(self, text: str, *, parse_mode: str | None = None, reply_markup: Any = None) -> None:
        await self._query.edit_message_text(text, parse_mode=parse_mode, reply_markup=_to_ptb_markup(reply_markup))

    async def delete_message(self) -> None:
        await self._query.delete_message()


class BotApiBackend(TelegramBackend):
    """Telegram backend using the HTTP Bot API via python-telegram-bot polling."""

    def __init__(self, config: ConfigWrapper) -> None:
        self._config = config
        self._ready_callback: Callable[[], None] | None = None
        self._bot_id: int = 0

        app_builder = Application.builder()
        (
            app_builder.base_url(config.bot_config.api_url)
            .connection_pool_size(265)
            .pool_timeout(1)
            .connect_timeout(10)
            .read_timeout(45)
            .write_timeout(60)
            .media_write_timeout(240)
            .concurrent_updates(2)
            .get_updates_connection_pool_size(4)
            .get_updates_pool_timeout(1)
            .get_updates_connect_timeout(10)
            .get_updates_read_timeout(45)
            .get_updates_write_timeout(60)
            .token(config.secrets.token)
            .post_init(self._post_init)
        )

        proxy_uri = ""
        if config.secrets.proxy_login and config.secrets.proxy_password:
            proxy_creds = f"{urllib.parse.quote(config.secrets.proxy_login, safe='')}:{urllib.parse.quote(config.secrets.proxy_password, safe='')}@"
        else:
            proxy_creds = ""

        if config.bot_config.socks_proxy:
            proxy_uri = f"socks5://{proxy_creds}{config.bot_config.socks_proxy}"

        if config.bot_config.http_proxy:
            proxy_uri = f"http://{proxy_creds}{config.bot_config.http_proxy}"

        if proxy_uri:
            app_builder.proxy(proxy_uri).get_updates_proxy(proxy_uri)

        self._application: Application = app_builder.build()  # type: ignore[type-arg]

    async def _post_init(self, application: Application) -> None:  # type: ignore[type-arg]
        self._bot_id = application.bot.id
        if self._ready_callback is None:
            msg = "Backend run without setup()"
            raise RuntimeError(msg)
        self._ready_callback()

    @property
    def bot_id(self) -> int:
        return self._bot_id

    def setup(self, registry: HandlerRegistry) -> None:
        """Register the neutral handler table as python-telegram-bot handlers."""
        self._application.add_handler(MessageHandler(~filters.Chat(self._config.secrets.chat_id), self._wrap(registry.unknown_chat)))

        for registration in registry.callbacks:
            self._application.add_handler(CallbackQueryHandler(self._wrap(registration.handler), pattern=registration.pattern.pattern if registration.pattern is not None else None))

        for command_registration in registry.commands:
            self._application.add_handler(CommandHandler(command_registration.name, self._wrap(command_registration.handler), block=not command_registration.parallel))

        if registry.catch_all_command is not None:
            self._application.add_handler(MessageHandler(filters.COMMAND, self._wrap(registry.catch_all_command), block=False))

        if registry.document is not None:
            self._application.add_handler(MessageHandler(filters.Document.ALL & (~filters.COMMAND), self._wrap(registry.document), block=False))

        if registry.unknown_text is not None:
            self._application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self._wrap(registry.unknown_text), block=False))

        self._application.add_error_handler(self._on_error)

    def _wrap(self, handler: Handler) -> Callable[[Update, Any], Coroutine[Any, Any, Any]]:
        async def ptb_handler(update: Update, _context: Any) -> None:
            await handler(self._to_update(update))

        return ptb_handler

    @staticmethod
    async def _on_error(_: object, context: Any) -> None:
        logger.error(msg="Exception while handling an update:", exc_info=context.error)

    def _to_update(self, update: Update) -> TgUpdate:
        message: TgMessage | None = None
        if update.effective_message is not None:
            message = _PTBMessage(self, update.effective_message)
        query: TgCallbackQuery | None = None
        if update.callback_query is not None:
            query = _PTBCallbackQuery(self, update.callback_query)
        chat = update.effective_chat
        return TgUpdate(
            effective_message=message,
            callback_query=query,
            effective_chat_id=chat.id if chat is not None else None,
            chat_is_group=bool(chat is not None and chat.type in (telegram.Chat.GROUP, telegram.Chat.SUPERGROUP)),
            chat_username=getattr(chat, "username", None),
        )

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: Any = None,
        disable_notification: bool = False,
        message_thread_id: int | None = None,
    ) -> TgMessage:
        message = await self._application.bot.send_message(
            chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=_to_ptb_markup(reply_markup),
            disable_notification=disable_notification,
            message_thread_id=message_thread_id,
        )
        return _PTBMessage(self, message)

    async def send_photo(
        self,
        chat_id: int,
        photo: Any,
        *,
        caption: str = "",
        parse_mode: str | None = None,
        reply_markup: Any = None,
        disable_notification: bool = False,
        message_thread_id: int | None = None,
        caption_entities: list[TgMessageEntity] | None = None,
    ) -> TgMessage:
        message = await self._application.bot.send_photo(
            chat_id,
            photo=photo,
            caption=caption,
            parse_mode=parse_mode,
            reply_markup=_to_ptb_markup(reply_markup),
            disable_notification=disable_notification,
            message_thread_id=message_thread_id,
            caption_entities=[MessageEntity(entity.type, entity.offset, entity.length) for entity in caption_entities] if caption_entities else None,
        )
        return _PTBMessage(self, message)

    async def send_video(
        self,
        chat_id: int,
        video: Any,
        *,
        thumbnail: Any = None,
        width: int = 0,
        height: int = 0,
        caption: str = "",
        parse_mode: str | None = None,
        disable_notification: bool = False,
        filename: str | None = None,
    ) -> TgMessage:
        message = await self._application.bot.send_video(
            chat_id,
            video=telegram.InputFile(video, filename=filename) if isinstance(video, bytes) and filename else video,
            thumbnail=thumbnail,
            width=width or None,
            height=height or None,
            caption=caption,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            write_timeout=600,
        )
        return _PTBMessage(self, message)

    async def send_media_group(self, chat_id: int, media: list[TgInputMedia], *, disable_notification: bool = False) -> list[TgMessage]:
        messages = await self._application.bot.send_media_group(
            chat_id,
            media=[_ptb_input_media(item) for item in media],
            disable_notification=disable_notification,
        )
        return [_PTBMessage(self, message) for message in messages]

    async def send_chat_action(self, chat_id: int, action: ChatAction, message_thread_id: int | None = None) -> None:
        await self._application.bot.send_chat_action(chat_id, action=_PTB_CHAT_ACTIONS[action], message_thread_id=message_thread_id)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self._application.bot.delete_message(chat_id, message_id)
        except BadRequest as ex:
            raise TgApiError(str(ex)) from ex

    async def pin_chat_message(self, chat_id: int, message_id: int, disable_notification: bool = False) -> None:
        await self._application.bot.pin_chat_message(chat_id, message_id, disable_notification=disable_notification)

    async def unpin_chat_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self._application.bot.unpin_chat_message(chat_id, message_id)
        except BadRequest as ex:
            raise TgApiError(str(ex)) from ex

    async def unpin_all_chat_messages(self, chat_id: int) -> None:
        await self._application.bot.unpin_all_chat_messages(chat_id)

    async def set_my_commands(self, commands: list[TgBotCommand]) -> None:
        await self._application.bot.set_my_commands([BotCommand(command.command, command.description) for command in commands])

    def run(self, ready_callback: Callable[[], None]) -> None:
        """Start polling; blocks until interrupted (handles SIGINT/SIGTERM natively)."""
        self._ready_callback = ready_callback
        self._application.run_polling(allowed_updates=Update.ALL_TYPES)

    async def shutdown(self) -> None:
        await self._application.stop()
