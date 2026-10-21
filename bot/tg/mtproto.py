"""Backend implementation speaking MTProto directly via Telethon (bot token login)."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from io import BytesIO
import logging
import signal
from typing import IO, TYPE_CHECKING, Any, Callable
import warnings

import httpx
from telethon import TelegramClient, events, functions, types  # type: ignore[import-untyped]
from telethon.errors import RPCError  # type: ignore[import-untyped]
from telethon.network import ConnectionTcpIntermediate, ConnectionTcpMTProxyRandomizedIntermediate  # type: ignore[import-untyped]
from telethon.sessions import StringSession  # type: ignore[import-untyped]

from tg.base import (
    ChatAction,
    HandlerRegistry,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaKind,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    TelegramBackend,
    TgApiError,
    TgCallbackQuery,
    TgDocument,
    TgInputMedia,
    TgMessage,
    TgMessageEntity,
    TgUpdate,
    resolve_callback_handler,
    resolve_message_handler,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from configuration import ConfigWrapper
    from tg.base import TgBotCommand

logger = logging.getLogger(__name__)

_TYPING_ACTIONS = {
    ChatAction.TYPING: types.SendMessageTypingAction,
    ChatAction.UPLOAD_PHOTO: types.SendMessageUploadPhotoAction,
    ChatAction.RECORD_VIDEO: types.SendMessageRecordVideoAction,
    ChatAction.UPLOAD_DOCUMENT: types.SendMessageUploadDocumentAction,
}

_ENTITY_TO_NAME: list[tuple[type[Any], str]] = [
    (types.MessageEntityBold, "bold"),
    (types.MessageEntityItalic, "italic"),
    (types.MessageEntityUnderline, "underline"),
    (types.MessageEntityStrike, "strikethrough"),
    (types.MessageEntityCode, "code"),
    (types.MessageEntityPre, "pre"),
    (types.MessageEntityTextUrl, "text_url"),
    (types.MessageEntityUrl, "url"),
]

_NAME_TO_ENTITY: dict[str, Any] = {name: cls for cls, name in _ENTITY_TO_NAME}


def _parse_mode(parse_mode: str | None) -> str | None:
    if parse_mode == "HTML":
        return "html"
    if parse_mode == "MarkdownV2":
        # Bot API MarkdownV2 payloads arrive pre-escaped; MTProto custom markdown would double-escape them.
        return None
    return None


def _utf16_slice(text: str, offset: int, length: int) -> str:
    units = text.encode("utf-16-le")
    return units[offset * 2 : (offset + length) * 2].decode("utf-16-le", errors="ignore")


def _tl_markup(markup: Any) -> Any:
    if markup is None:
        return None
    if isinstance(markup, ReplyKeyboardRemove):
        return types.ReplyKeyboardHide(selective=False)
    if isinstance(markup, ReplyKeyboardMarkup):
        return types.ReplyKeyboardMarkup(
            rows=[types.KeyboardButtonRow(buttons=[types.KeyboardButton(text=button, type=types.ButtonTypeDefault()) for button in row]) for row in markup.keyboard],
            resize=markup.resize_keyboard,
            single_use=False,
            selective=False,
        )
    if isinstance(markup, InlineKeyboardMarkup):
        return types.ReplyInlineMarkup(
            rows=[
                types.KeyboardInlineButtonRow(
                    buttons=[types.KeyboardInlineButton(text=button.text, type=types.InlineButtonTypeCallback(data=(button.callback_data or "").encode())) for button in row],
                )
                for row in markup.inline_keyboard
            ],
        )
    msg = f"Unsupported reply markup: {markup!r}"
    raise TypeError(msg)
    return None


def _neutral_markup(markup: Any) -> InlineKeyboardMarkup | None:
    if isinstance(markup, types.ReplyInlineMarkup):
        rows = []
        for row in markup.rows:
            buttons = []
            for button in row.buttons:
                data = None
                if isinstance(button, types.KeyboardInlineButton) and isinstance(button.type, types.InlineButtonTypeCallback):
                    data = button.type.data.decode("utf-8", "replace") if button.type.data else None
                    buttons.append(InlineKeyboardButton(button.text, data))
            rows.append(buttons)
        return InlineKeyboardMarkup(rows)
    return None


def _neutral_entities(entities: Sequence[Any] | None) -> list[TgMessageEntity]:
    result = []
    for entity in entities or []:
        for cls, name in _ENTITY_TO_NAME:
            if isinstance(entity, cls):
                result.append(TgMessageEntity(name, entity.offset, entity.length))
                break
    return result


def _tl_entities(entities: list[TgMessageEntity] | None) -> list[Any] | None:
    if not entities:
        return None
    result = []
    for entity in entities:
        entity_cls = _NAME_TO_ENTITY.get(entity.type)
        if entity_cls is not None:
            result.append(entity_cls(offset=entity.offset, length=entity.length))
    return result or None


def _document_file_name(media: Any) -> str | None:
    for attribute in getattr(media, "attributes", None) or []:
        if isinstance(attribute, types.DocumentAttributeFilename):
            return str(attribute.file_name)
    return None


class _MTLDocument(TgDocument):
    def __init__(self, client: TelegramClient, media: Any) -> None:
        super().__init__(_document_file_name(media))
        self._client = client
        self._media = media

    async def download(self) -> bytes:
        data = await self._client.download_media(self._media, file=bytes)
        if data is None:
            msg = f"Failed to download document {self.file_name}"
            raise TgApiError(msg)
        return bytes(data)


class _MTLMessage(TgMessage):
    def __init__(self, backend: MTProtoBackend, message: Any) -> None:
        super().__init__(message.chat_id, message.id)
        self._backend = backend
        self._message = message

    @property
    def text(self) -> str | None:
        return None if self._message.media else self._message.message

    @property
    def caption(self) -> str | None:
        return self._message.message if self._message.media else None

    @property
    def entities(self) -> list[TgMessageEntity]:
        return [] if self._message.media else _neutral_entities(self._message.entities)

    @property
    def caption_entities(self) -> list[TgMessageEntity]:
        return _neutral_entities(self._message.entities) if self._message.media else []

    @property
    def document(self) -> TgDocument | None:
        if isinstance(self._message.file, types.Document):
            return _MTLDocument(self._backend._client, self._message.file)  # noqa: SLF001
        return None

    @property
    def reply_markup(self) -> InlineKeyboardMarkup | None:
        return _neutral_markup(self._message.reply_markup)

    async def get_reply_message(self) -> TgMessage | None:
        reply = await self._message.get_reply_message()
        if reply is None:
            return None
        return _MTLMessage(self._backend, reply)

    @property
    def sender_is_bot(self) -> bool:
        return bool(self._message.out)

    def extract_entity_text(self, entity: TgMessageEntity, caption: bool = False) -> str:  # noqa: ARG002
        # MTProto stores caption and text in the same field, entities address it directly.
        source = self._message.message
        if source is None:
            return ""
        return _utf16_slice(source, entity.offset, entity.length).strip()

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
        if not do_quote:
            return await self._backend.send_message(self.chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup, disable_notification=disable_notification)
        return _MTLMessage(self._backend, await self._message.reply(text, parse_mode=_parse_mode(parse_mode), buttons=_tl_markup(reply_markup), silent=disable_notification))

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
        if not do_quote:
            return await self._backend.send_photo(
                self.chat_id, photo, caption=caption or "", parse_mode=parse_mode, reply_markup=reply_markup, disable_notification=disable_notification, caption_entities=caption_entities
            )
        return _MTLMessage(
            self._backend,
            await self._message.reply(
                file=photo,
                caption=caption,
                parse_mode=_parse_mode(parse_mode),
                buttons=_tl_markup(reply_markup),
                silent=disable_notification,
                formatting_entities=_tl_entities(caption_entities),
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
        kwargs: dict[str, Any] = {"file": video, "supports_streaming": True}
        if thumbnail is not None:
            kwargs["thumb"] = thumbnail
        if width and height:
            kwargs["attributes"] = [types.DocumentAttributeVideo(duration=0, w=width, h=height, round_message=False, supports_streaming=True)]
        if not do_quote:
            return await self._backend.send_video(
                self.chat_id, video, thumbnail=thumbnail, width=width, height=height, caption=caption, parse_mode=parse_mode, disable_notification=disable_notification
            )
        return _MTLMessage(self._backend, await self._message.reply(**kwargs, caption=caption, parse_mode=_parse_mode(parse_mode), silent=disable_notification))

    async def reply_media_group(self, media: list[TgInputMedia], *, disable_notification: bool = False, do_quote: bool = False) -> list[TgMessage]:
        return await self._backend.send_media_group(self.chat_id, media, disable_notification=disable_notification, reply_to=self._message if do_quote else None)

    async def edit_text(self, text: str, *, parse_mode: str | None = None, reply_markup: Any = None) -> None:
        await self._message.edit(text=text, parse_mode=_parse_mode(parse_mode), buttons=_tl_markup(reply_markup))

    async def edit_caption(self, caption: str, *, parse_mode: str | None = None, reply_markup: Any = None) -> None:
        await self._message.edit(text=caption, parse_mode=_parse_mode(parse_mode), buttons=_tl_markup(reply_markup))

    async def edit_media(self, photo: Any) -> None:
        await self._message.edit(file=photo)

    async def delete(self) -> None:
        await self._message.delete()


class _MTLCallbackQuery(TgCallbackQuery):
    def __init__(self, backend: MTProtoBackend, event: Any) -> None:
        message: TgMessage | None = None
        if event.message is not None:
            message = _MTLMessage(backend, event.message)
        data = event.data.decode("utf-8", "replace") if event.data else None
        super().__init__(message, data)
        self._event = event

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        try:
            await self._event.answer(text, alert=show_alert)
        except RPCError as ex:
            logger.debug("Failed to answer callback query: %s", ex)

    async def edit_message_text(self, text: str, *, parse_mode: str | None = None, reply_markup: Any = None) -> None:
        await self._event.edit(text=text, parse_mode=_parse_mode(parse_mode), buttons=_tl_markup(reply_markup))

    async def delete_message(self) -> None:
        await self._event.delete()


class MTProtoBackend(TelegramBackend):
    """Telegram backend speaking MTProto to Telegram data centers directly."""

    def __init__(self, config: ConfigWrapper) -> None:
        self._config = config
        self._bot_id: int = 0
        self._registry: HandlerRegistry | None = None

        connection, proxy = self._build_proxy()
        with warnings.catch_warnings():
            # Telethon warns about python-socks for any proxy tuple, but MTProxy
            # works in-protocol (InitConnection InputClientProxy) without it.
            warnings.filterwarnings("ignore", message="proxy argument will be ignored because python-socks is not installed")
            self._client = TelegramClient(
                StringSession(),
                config.bot_config.api_id,
                config.bot_config.api_hash,
                connection=connection,
                proxy=proxy,
                request_retries=3,
                connection_retries=3,
                retry_delay=1,
                auto_reconnect=True,
            )

    def _build_proxy(self) -> tuple[Any, Any]:
        proxy_string = self._config.bot_config.mtproto_proxy
        if proxy_string:
            host, port, secret = proxy_string.rsplit(":", 2)
            logger.info("MTProto backend: using MTProxy at %s:%s", host, port)
            return ConnectionTcpMTProxyRandomizedIntermediate, (host, int(port), secret)
        if self._config.bot_config.socks_proxy or self._config.bot_config.http_proxy:
            logger.warning("MTProto backend ignores socks_proxy/http_proxy (http backend only). Use mtproto_proxy with a MTProxy server instead.")
        return ConnectionTcpIntermediate, None

    @property
    def bot_id(self) -> int:
        return self._bot_id

    def setup(self, registry: HandlerRegistry) -> None:
        self._registry = registry
        chat = self._config.secrets.chat_id
        self._client.add_event_handler(self._on_other_chat, events.NewMessage(incoming=True, blacklist_chats=True, chats=chat))
        self._client.add_event_handler(self._on_message, events.NewMessage(incoming=True, chats=chat))
        self._client.add_event_handler(self._on_callback, events.CallbackQuery(chats=chat))

    def run(self, ready_callback: Callable[[], None]) -> None:
        """Connect over MTProto and process updates until SIGINT/SIGTERM."""
        try:
            asyncio.run(self._run_async(ready_callback))
        except KeyboardInterrupt:
            logger.info("MTProto backend interrupted")

    async def _run_async(self, ready_callback: Callable[[], None]) -> None:
        try:
            await self._client.start(bot_token=self._config.secrets.token)
        except Exception:
            logger.exception("MTProto login failed, check api_id/api_hash/bot_token")
            raise
        self._bot_id = (await self._client.get_me()).id

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)

        ready_callback()
        await stop.wait()
        await self._client.disconnect()

    def _to_message_update(self, event: Any) -> TgUpdate:
        chat_username = None
        with suppress(Exception):
            chat_username = getattr(event.chat, "username", None)
        return TgUpdate(
            effective_message=_MTLMessage(self, event.message),
            callback_query=None,
            effective_chat_id=event.chat_id,
            chat_is_group=bool(event.is_group),
            chat_username=chat_username,
        )

    def _to_callback_update(self, event: Any) -> TgUpdate:
        return TgUpdate(
            effective_message=_MTLMessage(self, event.message) if event.message is not None else None,
            callback_query=_MTLCallbackQuery(self, event),
            effective_chat_id=event.chat_id,
            chat_is_group=False,
            chat_username=None,
        )

    async def _on_other_chat(self, event: events.NewMessage.Event) -> None:
        if self._registry is not None:
            await self._registry.unknown_chat(self._to_message_update(event))

    async def _on_message(self, event: events.NewMessage.Event) -> None:
        if self._registry is None:
            return
        text = event.message.message
        handler = resolve_message_handler(self._registry, text or None)
        if handler is None:
            return
        await handler(self._to_message_update(event))

    async def _on_callback(self, event: events.CallbackQuery.Event) -> None:
        if self._registry is None:
            return
        data = event.data.decode("utf-8", "replace") if event.data else None
        handler = resolve_callback_handler(self._registry, data)
        if handler is None:
            logger.debug("No callback handler matched query data: %s", data)
            return
        await handler(self._to_callback_update(event))

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
        message = await self._client.send_message(
            chat_id,
            text,
            reply_to=message_thread_id,
            parse_mode=_parse_mode(parse_mode),
            buttons=_tl_markup(reply_markup),
            silent=disable_notification,
        )
        return _MTLMessage(self, message)

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
        message = await self._client.send_file(
            chat_id,
            file=photo,
            caption=caption or None,
            parse_mode=_parse_mode(parse_mode),
            buttons=_tl_markup(reply_markup),
            silent=disable_notification,
            reply_to=message_thread_id,
            formatting_entities=_tl_entities(caption_entities),
        )
        return _MTLMessage(self, message)

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
        kwargs: dict[str, Any] = {"supports_streaming": True}
        if isinstance(video, bytes):
            kwargs["file"] = _named_bytes(video, filename)
        else:
            kwargs["file"] = video
        if thumbnail is not None:
            kwargs["thumb"] = _named_bytes(thumbnail, "thumb.jpg") if isinstance(thumbnail, bytes) else thumbnail
        if width and height:
            kwargs["attributes"] = [types.DocumentAttributeVideo(duration=0, w=width, h=height, round_message=False, supports_streaming=True)]
        message = await self._client.send_file(
            chat_id,
            caption=caption or None,
            parse_mode=_parse_mode(parse_mode),
            silent=disable_notification,
            **kwargs,
        )
        return _MTLMessage(self, message)

    async def send_media_group(self, chat_id: int, media: list[TgInputMedia], *, disable_notification: bool = False, reply_to: TgMessage | None = None) -> list[TgMessage]:
        if reply_to is None:
            try:
                return await self._send_multi_media(chat_id, media, disable_notification)
            except RPCError as ex:
                logger.warning("sendMultiMedia failed (%s), sending items one by one", ex)
        messages: list[TgMessage] = []
        for item in media:
            message = await self._send_single_media(chat_id, item, disable_notification, reply_to.message_id if reply_to is not None and not messages else None)
            messages.append(_MTLMessage(self, message))
        return messages

    async def _send_multi_media(self, chat_id: int, media: list[TgInputMedia], disable_notification: bool) -> list[TgMessage]:
        peer = await self._client.get_input_entity(chat_id)
        single_media = []
        for item in media:
            uploaded = await self._client.upload_file(item.media)
            single_media.append(types.InputSingleMedia(media=_input_media_for(item, uploaded), message=item.caption or ""))
        result = await self._client(functions.messages.SendMultiMediaRequest(peer=peer, multi_media=single_media, silent=disable_notification))
        updates = getattr(result, "updates", None) or []
        messages: list[TgMessage] = [_MTLMessage(self, message) for message in updates if isinstance(message, types.Message)]
        return messages

    async def _send_single_media(self, chat_id: int, item: TgInputMedia, disable_notification: bool, reply_to_msg_id: int | None) -> Any:
        kwargs: dict[str, Any] = {"file": _named_bytes(item.media, item.filename), "silent": disable_notification, "reply_to": reply_to_msg_id}
        if item.caption:
            kwargs["caption"] = item.caption
        if item.kind is InputMediaKind.DOCUMENT:
            kwargs["force_document"] = True
        elif item.kind is InputMediaKind.VIDEO:
            kwargs["supports_streaming"] = True
        return await self._client.send_file(chat_id, **kwargs)

    async def send_chat_action(self, chat_id: int, action: ChatAction, message_thread_id: int | None = None) -> None:
        try:
            peer = await self._client.get_input_entity(chat_id)
            await self._client(functions.messages.SetTypingRequest(peer=peer, action=_TYPING_ACTIONS[action](), top_msg_id=message_thread_id))
        except RPCError as ex:
            logger.debug("Failed to send chat action: %s", ex)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self._client.delete_messages(chat_id, [message_id])
        except RPCError as ex:
            raise TgApiError(str(ex)) from ex

    async def pin_chat_message(self, chat_id: int, message_id: int, disable_notification: bool = False) -> None:  # noqa: ARG002
        try:
            await self._client.pin_message(chat_id, message_id)
        except RPCError as ex:
            raise TgApiError(str(ex)) from ex

    async def unpin_chat_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self._client.unpin_message(chat_id, message_id)
        except RPCError as ex:
            raise TgApiError(str(ex)) from ex

    async def unpin_all_chat_messages(self, chat_id: int) -> None:
        try:
            await self._client.unpin_message(chat_id)
        except RPCError as ex:
            raise TgApiError(str(ex)) from ex

    async def set_my_commands(self, commands: list[TgBotCommand]) -> None:
        try:
            await self._client(
                functions.bots.SetBotCommandsRequest(scope=types.BotCommandScopeDefault(), lang_code="", commands=[types.BotCommand(command.command, command.description) for command in commands])
            )
        except (RPCError, TypeError) as ex:
            logger.debug("bots.setBotCommands failed (%s), trying HTTP Bot API fallback", ex)
            await self._set_my_commands_http(commands)

    async def _set_my_commands_http(self, commands: list[TgBotCommand]) -> None:
        url = f"https://api.telegram.org/bot{self._config.secrets.token}/setMyCommands"
        payload = {"commands": [{"command": command.command, "description": command.description} for command in commands]}
        try:
            async with httpx.AsyncClient(timeout=15) as http_client:
                response = await http_client.post(url, json=payload)
                if response.status_code >= 400:  # noqa: PLR2004
                    logger.warning("Failed to set bot command menu via HTTP fallback: %s", response.text)
        except httpx.HTTPError as ex:
            logger.warning("Failed to set bot command menu via HTTP fallback: %s", ex)


def _named_bytes(data: bytes, name: str | None) -> IO[bytes]:
    bio = BytesIO(data)
    if name:
        bio.name = name
    return bio


def _input_media_for(item: TgInputMedia, uploaded: Any) -> Any:
    if item.kind is InputMediaKind.PHOTO:
        return types.InputMediaUploadedPhoto(file=uploaded)
    attributes = []
    if item.filename:
        attributes.append(types.DocumentAttributeFilename(file_name=item.filename))
    mime_type = "application/octet-stream"
    if item.kind is InputMediaKind.VIDEO:
        mime_type = "video/mp4"
        attributes.append(types.DocumentAttributeVideo(duration=0, w=0, h=0, round_message=False, supports_streaming=True))
    return types.InputMediaUploadedDocument(file=uploaded, mime_type=mime_type, attributes=attributes)
