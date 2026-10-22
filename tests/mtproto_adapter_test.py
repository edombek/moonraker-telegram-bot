import asyncio
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

from PIL import Image
from telethon import types
from telethon.network import ConnectionTcpIntermediate, ConnectionTcpMTProxyIntermediate, ConnectionTcpMTProxyRandomizedIntermediate

from tg.base import ChatAction, TgMessage
from tg.mtproto import MTProtoBackend, _as_photo_file, _MTLMessage


def make_message(media: object) -> MagicMock:
    message = MagicMock()
    message.chat_id = 42
    message.id = 7
    message.media = media
    return message


def make_backend(proxy: str = "") -> MTProtoBackend:
    config = MagicMock()
    config.bot_config.api_type = "mtproto"
    config.bot_config.api_id = 1
    config.bot_config.api_hash = "0" * 32
    config.bot_config.mtproto_proxy = proxy
    config.bot_config.socks_proxy = ""
    config.bot_config.http_proxy = ""
    return MTProtoBackend(config)


def test_proxy_connection_class_selection() -> None:
    dd = make_backend("proxy.host:1234:dd" + "ab" * 16)
    plain = make_backend("proxy.host:1234:02" + "cd" * 15)
    direct = make_backend("")
    assert dd._build_proxy()[0] is ConnectionTcpMTProxyRandomizedIntermediate
    assert plain._build_proxy()[0] is ConnectionTcpMTProxyIntermediate
    assert direct._build_proxy()[0] is ConnectionTcpIntermediate


def document_media(file_name: str = "cube.gcode") -> types.MessageMediaDocument:
    document = types.Document(
        id=1,
        access_hash=2,
        file_reference=b"",
        date=datetime.now(tz=timezone.utc),
        mime_type="text/plain",
        size=123,
        dc_id=1,
        attributes=[types.DocumentAttributeFilename(file_name=file_name)],
    )
    return types.MessageMediaDocument(document=document)


def test_document_detected_from_message_media_document() -> None:
    message = _MTLMessage(make_backend(), make_message(document_media()))
    doc = message.document
    assert doc is not None
    assert doc.file_name == "cube.gcode"


def test_document_none_for_plain_text() -> None:
    message = _MTLMessage(make_backend(), make_message(None))
    assert message.document is None


def test_document_none_for_photo_media() -> None:
    photo = types.Photo(id=1, access_hash=2, file_reference=b"", date=datetime.now(tz=timezone.utc), sizes=[], dc_id=1)
    message = _MTLMessage(make_backend(), make_message(types.MessageMediaPhoto(photo=photo)))
    assert message.document is None


def test_reply_media_group_passes_neutral_reply_to() -> None:
    backend = make_backend()
    backend.send_media_group = AsyncMock()  # type: ignore[method-assign]
    message = _MTLMessage(backend, make_message(None))
    asyncio.run(message.reply_media_group([], do_quote=True))
    _, kwargs = backend.send_media_group.call_args
    reply_to = kwargs["reply_to"]
    assert isinstance(reply_to, TgMessage)
    assert reply_to.message_id == 7


def _image_bytes(fmt: str) -> bytes:
    bio = BytesIO()
    Image.new("RGB", (8, 8), "red").save(bio, format=fmt)
    return bio.getvalue()


def test_as_photo_file_converts_webp_to_png() -> None:
    converted = _as_photo_file(BytesIO(_image_bytes("WEBP")))
    assert Image.open(converted).format == "PNG"
    assert converted.name == "photo.png"


def test_as_photo_file_keeps_jpeg_and_non_images() -> None:
    jpeg = BytesIO(_image_bytes("JPEG"))
    jpeg.name = "photo.jpg"
    assert _as_photo_file(jpeg) is jpeg
    raw = BytesIO(b"not an image")
    assert _as_photo_file(raw) is raw


def test_as_photo_file_fixes_missing_or_wrong_extension() -> None:
    # camera snapshots arrive as unnamed jpeg bytes -> telethon needs an extension
    unnamed = BytesIO(_image_bytes("JPEG"))
    renamed = _as_photo_file(unnamed)
    assert renamed is not unnamed
    assert renamed.name == "photo.jpg"
    assert Image.open(renamed).format == "JPEG"
    # moonraker thumbnails are png bytes but named ".webp"
    thumb = BytesIO(_image_bytes("PNG"))
    thumb.name = ".webp"
    renamed = _as_photo_file(thumb)
    assert renamed.name == "photo.png"
    assert Image.open(renamed).format == "PNG"


def test_send_chat_action_builds_tl_actions() -> None:
    backend = make_backend()
    backend._client = AsyncMock()
    for action in ChatAction:
        asyncio.run(backend.send_chat_action(42, action))  # must not raise TypeError
    assert backend._client.call_count == len(ChatAction)
