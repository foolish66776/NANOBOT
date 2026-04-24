"""Telegram channel implementation using python-telegram-bot."""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger
from pydantic import Field
from telegram import BotCommand, ReactionTypeEmoji, ReplyParameters, Update
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.command.builtin import build_help_text
from nanobot.config.paths import get_media_dir
from nanobot.config.schema import Base
from nanobot.security.network import validate_url_target
from nanobot.utils.helpers import split_message

TELEGRAM_MAX_MESSAGE_LEN = 4000  # Telegram message character limit
TELEGRAM_REPLY_CONTEXT_MAX_LEN = TELEGRAM_MAX_MESSAGE_LEN  # Max length for reply context in user message

# Per-handler context: tracks the active bot Application and business_line.
# Set at the top of every message/command handler closure so all awaited methods
# (typing, reactions, media download, …) automatically use the right bot.
# asyncio.create_task() copies the current context, so sub-tasks (e.g. typing loop)
# also inherit the correct value.
_current_bot_ctx: ContextVar[dict | None] = ContextVar("_current_bot_ctx", default=None)
# Set to True when per-bot ACL has already been checked in a multi-bot wrapper,
# so the base-class is_allowed() check is bypassed.
_acl_approved: ContextVar[bool] = ContextVar("_acl_approved", default=False)


def _tg_sender_allowed(sender_id: str, allow_from: list[str]) -> bool:
    """Check sender against a bot-specific allow_from list (id, id|username, or '*')."""
    if not allow_from:
        return False
    if "*" in allow_from:
        return True
    sender_str = str(sender_id)
    if sender_str in allow_from:
        return True
    if sender_str.count("|") == 1:
        sid, username = sender_str.split("|", 1)
        return sid in allow_from or username in allow_from
    return False


def _escape_telegram_html(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tool_hint_to_telegram_blockquote(text: str) -> str:
    """Render tool hints as an expandable blockquote (collapsed by default)."""
    return f"<blockquote expandable>{_escape_telegram_html(text)}</blockquote>" if text else ""


def _strip_md(s: str) -> str:
    """Strip markdown inline formatting from text."""
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'__(.+?)__', r'\1', s)
    s = re.sub(r'~~(.+?)~~', r'\1', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    return s.strip()


def _render_table_box(table_lines: list[str]) -> str:
    """Convert markdown pipe-table to compact aligned text for <pre> display."""

    def dw(s: str) -> int:
        return sum(2 if unicodedata.east_asian_width(c) in ('W', 'F') else 1 for c in s)

    rows: list[list[str]] = []
    has_sep = False
    for line in table_lines:
        cells = [_strip_md(c) for c in line.strip().strip('|').split('|')]
        if all(re.match(r'^:?-+:?$', c) for c in cells if c):
            has_sep = True
            continue
        rows.append(cells)
    if not rows or not has_sep:
        return '\n'.join(table_lines)

    ncols = max(len(r) for r in rows)
    for r in rows:
        r.extend([''] * (ncols - len(r)))
    widths = [max(dw(r[c]) for r in rows) for c in range(ncols)]

    def dr(cells: list[str]) -> str:
        return '  '.join(f'{c}{" " * (w - dw(c))}' for c, w in zip(cells, widths))

    out = [dr(rows[0])]
    out.append('  '.join('─' * w for w in widths))
    for row in rows[1:]:
        out.append(dr(row))
    return '\n'.join(out)


def _markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram-safe HTML.
    """
    if not text:
        return ""

    # 1. Extract and protect code blocks (preserve content from other processing)
    code_blocks: list[str] = []
    def save_code_block(m: re.Match) -> str:
        code_blocks.append(m.group(1))
        return f"\x00CB{len(code_blocks) - 1}\x00"

    text = re.sub(r'```[\w]*\n?([\s\S]*?)```', save_code_block, text)

    # 1.5. Convert markdown tables to box-drawing (reuse code_block placeholders)
    lines = text.split('\n')
    rebuilt: list[str] = []
    li = 0
    while li < len(lines):
        if re.match(r'^\s*\|.+\|', lines[li]):
            tbl: list[str] = []
            while li < len(lines) and re.match(r'^\s*\|.+\|', lines[li]):
                tbl.append(lines[li])
                li += 1
            box = _render_table_box(tbl)
            if box != '\n'.join(tbl):
                code_blocks.append(box)
                rebuilt.append(f"\x00CB{len(code_blocks) - 1}\x00")
            else:
                rebuilt.extend(tbl)
        else:
            rebuilt.append(lines[li])
            li += 1
    text = '\n'.join(rebuilt)

    # 2. Extract and protect inline code
    inline_codes: list[str] = []
    def save_inline_code(m: re.Match) -> str:
        inline_codes.append(m.group(1))
        return f"\x00IC{len(inline_codes) - 1}\x00"

    text = re.sub(r'`([^`]+)`', save_inline_code, text)

    # 3. Headers # Title -> just the title text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'\1', text, flags=re.MULTILINE)

    # 4. Blockquotes > text -> just the text (before HTML escaping)
    text = re.sub(r'^>\s*(.*)$', r'\1', text, flags=re.MULTILINE)

    # 5. Escape HTML special characters
    text = _escape_telegram_html(text)

    # 6. Links [text](url) - must be before bold/italic to handle nested cases
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 7. Bold **text** or __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # 8. Italic _text_ (avoid matching inside words like some_var_name)
    text = re.sub(r'(?<![a-zA-Z0-9])_([^_]+)_(?![a-zA-Z0-9])', r'<i>\1</i>', text)

    # 9. Strikethrough ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 10. Bullet lists - item -> • item
    text = re.sub(r'^[-*]\s+', '• ', text, flags=re.MULTILINE)

    # 11. Restore inline code with HTML tags
    for i, code in enumerate(inline_codes):
        # Escape HTML in code content
        escaped = _escape_telegram_html(code)
        text = text.replace(f"\x00IC{i}\x00", f"<code>{escaped}</code>")

    # 12. Restore code blocks with HTML tags
    for i, code in enumerate(code_blocks):
        # Escape HTML in code content
        escaped = _escape_telegram_html(code)
        text = text.replace(f"\x00CB{i}\x00", f"<pre><code>{escaped}</code></pre>")

    return text


_SEND_MAX_RETRIES = 3
_SEND_RETRY_BASE_DELAY = 0.5  # seconds, doubled each retry
_STREAM_EDIT_INTERVAL_DEFAULT = 0.6  # min seconds between edit_message_text calls


@dataclass
class _StreamBuf:
    """Per-chat streaming accumulator for progressive message editing."""
    text: str = ""
    message_id: int | None = None
    last_edit: float = 0.0
    stream_id: str | None = None


class TeleBotConfig(Base):
    """Configuration for a single bot in multi-bot mode."""

    token: str = ""
    business_line: str = ""  # injected as metadata["business_line"] on every inbound message
    allow_from: list[str] = Field(default_factory=list)


class TelegramConfig(Base):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""
    allow_from: list[str] = Field(default_factory=list)
    # Multi-bot: if set, each entry spawns its own bot polling loop.
    # Overrides top-level token/allow_from when non-empty.
    bots: list[TeleBotConfig] = Field(default_factory=list)
    proxy: str | None = None
    reply_to_message: bool = False
    react_emoji: str = "👀"
    group_policy: Literal["open", "mention"] = "mention"
    connection_pool_size: int = 32
    pool_timeout: float = 5.0
    streaming: bool = True
    stream_edit_interval: float = Field(default=_STREAM_EDIT_INTERVAL_DEFAULT, ge=0.1)


async def _send_approved_preview(order_id: int, cfg, pool) -> None:
    """Send the approved pre-shipment preview to the customer (or draft to Alessandro if not linked)."""
    from nanobot.business.foolish.telegram import send_to_customer, send_to_alessandro as _send_ale
    row = await pool.fetchrow(
        "SELECT body, recipient FROM foolish.messages WHERE order_id = $1 AND stage = 'preview' AND approved_by_alessandro IS NULL ORDER BY created_at DESC LIMIT 1",
        order_id,
    )
    if not row:
        await _send_ale(cfg, f"⚠️ Bozza preview non trovata per ordine #{order_id}.")
        return
    body = row["body"]
    # Get customer telegram id
    order_row = await pool.fetchrow("SELECT customer_telegram_id, customer_email FROM foolish.orders WHERE id = $1", order_id)
    tg_id = order_row["customer_telegram_id"] if order_row else None
    if tg_id:
        await send_to_customer(cfg, tg_id, body)
        await pool.execute(
            "UPDATE foolish.messages SET approved_by_alessandro = TRUE, sent_at = NOW() WHERE order_id = $1 AND stage = 'preview' AND approved_by_alessandro IS NULL",
            order_id,
        )
        await _send_ale(cfg, f"✅ Preview inviata al cliente per ordine #{order_id}.")
    else:
        await _send_ale(cfg, f"ℹ️ Cliente ordine #{order_id} non ha Telegram collegato.\n\nInvia tu questo messaggio:\n\n<pre>{body}</pre>")


async def _handle_foolish_callback(update, context) -> None:
    """Route Telegram callback_query updates to the Foolish Butcher pipeline."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""
    from_id = query.from_user.id if query.from_user else None

    try:
        import os
        from nanobot.business.foolish.config import get_config
        from nanobot.business.foolish.db import OrderRepo, MessageRepo, get_pool
        from nanobot.business.foolish.pipeline.eta_confirmed import handle_eta_confirmed
        from nanobot.business.foolish.telegram import send_to_alessandro

        cfg = get_config()

        if from_id != cfg.alessandro_chat_id:
            return

        parts = data.split(":")
        if len(parts) < 3:
            return
        action_ns, order_id_str, action = parts[0], parts[1], parts[2]
        order_id = int(order_id_str)
        pool = await get_pool(cfg.database_url)

        if action_ns == "eta":
            if action == "custom":
                await send_to_alessandro(
                    cfg,
                    f"Digita il numero di giorni per l'ordine #{order_id_str} (es: <code>eta {order_id_str} 12</code>):",
                )
                return
            eta_days = int(action)
            order_repo = OrderRepo(pool)
            message_repo = MessageRepo(pool)
            await handle_eta_confirmed(order_id, eta_days, cfg, order_repo, message_repo)
            await send_to_alessandro(
                cfg,
                f"✅ ETA {eta_days}gg confermata per ordine #{order_id}. Messaggio pre-produzione inviato.",
            )

        elif action_ns == "match":
            from nanobot.business.foolish.pipeline.matching import confirm_matching, reject_matching
            if action == "approve":
                await confirm_matching(order_id, cfg, pool)
                await send_to_alessandro(cfg, f"✅ Matching ordine #{order_id} approvato. Fogli riservati. Bozza preview inviata.")
            elif action == "reject":
                await reject_matching(order_id, cfg, pool)

        elif action_ns == "preview":
            if action == "approve":
                await _send_approved_preview(order_id, cfg, pool)
            elif action == "edit":
                await send_to_alessandro(cfg, f"Inviami il testo corretto per la preview dell'ordine #{order_id} e lo sostituirò.")

    except Exception:
        logger.exception("Error handling foolish callback_query: {}", data)


class TelegramChannel(BaseChannel):
    """
    Telegram channel using long polling.

    Simple and reliable - no webhook/public IP needed.
    """

    name = "telegram"
    display_name = "Telegram"

    # Commands registered with Telegram's command menu
    BOT_COMMANDS = [
        BotCommand("start", "Start the bot"),
        BotCommand("new", "Start a new conversation"),
        BotCommand("stop", "Stop the current task"),
        BotCommand("restart", "Restart the bot"),
        BotCommand("status", "Show bot status"),
        BotCommand("dream", "Run Dream memory consolidation now"),
        BotCommand("dream_log", "Show the latest Dream memory change"),
        BotCommand("dream_restore", "Restore Dream memory to an earlier version"),
        BotCommand("help", "Show available commands"),
    ]

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return TelegramConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus):
        if isinstance(config, dict):
            config = TelegramConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: TelegramConfig = config
        self._app: Application | None = None  # primary app (first bot or single-bot)
        self._apps_list: list[tuple[Application, str, list[str]]] = []  # (app, business_line, allow_from)
        self._chat_to_app: dict[str, Application] = {}  # chat_id -> app for outbound routing
        self._app_identities: dict[int, tuple[int | None, str | None]] = {}  # id(app) -> (bot_id, username)
        self._chat_ids: dict[str, int] = {}  # Map sender_id to chat_id for replies
        self._typing_tasks: dict[str, asyncio.Task] = {}  # chat_id -> typing loop task
        self._media_group_buffers: dict[str, dict] = {}
        self._media_group_tasks: dict[str, asyncio.Task] = {}
        self._message_threads: dict[tuple[str, int], int] = {}
        self._stream_bufs: dict[str, _StreamBuf] = {}  # chat_id -> streaming state

    def is_allowed(self, sender_id: str) -> bool:
        """Preserve Telegram's legacy id|username allowlist matching.

        In multi-bot mode the per-bot wrapper already ran ACL before calling
        _handle_message, so we skip the redundant check via _acl_approved.
        """
        if _acl_approved.get():
            return True

        if super().is_allowed(sender_id):
            return True

        allow_list = getattr(self.config, "allow_from", [])
        if not allow_list or "*" in allow_list:
            return False

        sender_str = str(sender_id)
        if sender_str.count("|") != 1:
            return False

        sid, username = sender_str.split("|", 1)
        if not sid.isdigit() or not username:
            return False

        return sid in allow_list or username in allow_list

    def _app_for_chat(self, chat_id: str) -> Application | None:
        """Return the Application that serves *chat_id* (for outbound routing)."""
        return self._chat_to_app.get(str(chat_id)) or self._app

    def _ctx_app(self) -> Application | None:
        """Return the Application from the current handler context (inbound path)."""
        ctx = _current_bot_ctx.get()
        return ctx["app"] if ctx else self._app

    @staticmethod
    def _normalize_telegram_command(content: str) -> str:
        """Map Telegram-safe command aliases back to canonical nanobot commands."""
        if not content.startswith("/"):
            return content
        if content == "/dream_log" or content.startswith("/dream_log "):
            return content.replace("/dream_log", "/dream-log", 1)
        if content == "/dream_restore" or content.startswith("/dream_restore "):
            return content.replace("/dream_restore", "/dream-restore", 1)
        return content

    def _bot_specs(self) -> list[tuple[str, str, list[str]]]:
        """Return list of (token, business_line, allow_from) from config.

        Multi-bot mode: use config.bots (each entry is a TeleBotConfig).
        Single-bot mode: use config.token + config.allow_from with no business_line.
        """
        if self.config.bots:
            return [
                (b.token, b.business_line, b.allow_from)
                for b in self.config.bots
                if b.token
            ]
        if self.config.token:
            return [(self.config.token, "", list(self.config.allow_from))]
        return []

    def _build_application(self, token: str) -> Application:
        """Build a python-telegram-bot Application for one token."""
        proxy = self.config.proxy or None
        api_request = HTTPXRequest(
            connection_pool_size=self.config.connection_pool_size,
            pool_timeout=self.config.pool_timeout,
            connect_timeout=30.0,
            read_timeout=30.0,
            proxy=proxy,
        )
        poll_request = HTTPXRequest(
            connection_pool_size=4,
            pool_timeout=self.config.pool_timeout,
            connect_timeout=30.0,
            read_timeout=30.0,
            proxy=proxy,
        )
        return (
            Application.builder()
            .token(token)
            .request(api_request)
            .get_updates_request(poll_request)
            .build()
        )

    def _register_handlers(self, app: Application, business_line: str, allow_from: list[str]) -> None:
        """Register all message handlers on *app* with bot-specific context injected."""

        bl = business_line
        af = allow_from
        multi_bot = bool(self.config.bots)

        async def _wrap_msg(update, context):
            """Set per-bot context vars then delegate to _on_message."""
            _current_bot_ctx.set({"app": app, "business_line": bl})
            if multi_bot:
                # Per-bot ACL replaces the global is_allowed check.
                if update.message and update.effective_user:
                    sid = self._sender_id(update.effective_user)
                    if not _tg_sender_allowed(sid, af):
                        logger.warning(
                            "Access denied for sender {} (business_line={})", sid, bl
                        )
                        return
                _acl_approved.set(True)
                # Track outbound routing for this chat.
                if update.message:
                    self._chat_to_app[str(update.message.chat_id)] = app
            await self._on_message(update, context)

        async def _wrap_cmd(update, context):
            """Set per-bot context vars then delegate to _forward_command."""
            _current_bot_ctx.set({"app": app, "business_line": bl})
            if multi_bot:
                if update.message and update.effective_user:
                    sid = self._sender_id(update.effective_user)
                    if not _tg_sender_allowed(sid, af):
                        return
                _acl_approved.set(True)
                if update.message:
                    self._chat_to_app[str(update.message.chat_id)] = app
            await self._forward_command(update, context)

        app.add_error_handler(self._on_error)
        app.add_handler(MessageHandler(filters.Regex(r"^/start(?:@\w+)?$"), self._on_start))
        app.add_handler(MessageHandler(
            filters.Regex(r"^/(new|stop|restart|status|dream)(?:@\w+)?(?:\s+.*)?$"),
            _wrap_cmd,
        ))
        app.add_handler(MessageHandler(
            filters.Regex(r"^/(dream-log|dream_log|dream-restore|dream_restore)(?:@\w+)?(?:\s+.*)?$"),
            _wrap_cmd,
        ))
        app.add_handler(MessageHandler(filters.Regex(r"^/help(?:@\w+)?$"), self._on_help))
        app.add_handler(MessageHandler(
            (filters.TEXT | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL | filters.LOCATION)
            & ~filters.COMMAND,
            _wrap_msg,
        ))

        if bl == "foolish":
            async def _wrap_foolish_callback(update, context):
                _current_bot_ctx.set({"app": app, "business_line": bl})
                await _handle_foolish_callback(update, context)
            app.add_handler(CallbackQueryHandler(_wrap_foolish_callback))

    async def _run_bot(self, app: Application, business_line: str) -> None:
        """Initialise, start polling, and run *app* until self._running is False."""
        await app.initialize()
        await app.start()

        bot_info = await app.bot.get_me()
        self._app_identities[id(app)] = (
            getattr(bot_info, "id", None),
            getattr(bot_info, "username", None),
        )
        suffix = f" (business_line={business_line})" if business_line else ""
        logger.info("Telegram bot @{} connected{}", bot_info.username, suffix)

        try:
            await app.bot.set_my_commands(self.BOT_COMMANDS)
        except Exception as e:
            logger.warning("Failed to register bot commands: {}", e)

        await app.updater.start_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=False,
            error_callback=self._on_polling_error,
        )

        while self._running:
            await asyncio.sleep(1)

    async def start(self) -> None:
        """Start the Telegram bot(s) with long polling."""
        specs = self._bot_specs()
        if not specs:
            logger.error("Telegram: no bot token configured")
            return

        self._running = True

        for token, bl, af in specs:
            app = self._build_application(token)
            self._register_handlers(app, bl, af)
            self._apps_list.append((app, bl, af))
            if self._app is None:
                self._app = app  # backward compat: primary app is the first one

        logger.info("Starting {} Telegram bot(s)...", len(self._apps_list))

        # Run all bots concurrently; each _run_bot loops until _running=False.
        await asyncio.gather(
            *[self._run_bot(app, bl) for app, bl, _ in self._apps_list],
            return_exceptions=True,
        )

    async def stop(self) -> None:
        """Stop all Telegram bots."""
        self._running = False

        for chat_id in list(self._typing_tasks):
            self._stop_typing(chat_id)

        for task in self._media_group_tasks.values():
            task.cancel()
        self._media_group_tasks.clear()
        self._media_group_buffers.clear()

        for app, _, __ in self._apps_list:
            try:
                logger.info("Stopping Telegram bot...")
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception as e:
                logger.warning("Error stopping Telegram bot: {}", e)

        self._apps_list.clear()
        self._app = None

    @staticmethod
    def _get_media_type(path: str) -> str:
        """Guess media type from file extension."""
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        if ext in ("jpg", "jpeg", "png", "gif", "webp"):
            return "photo"
        if ext == "ogg":
            return "voice"
        if ext in ("mp3", "m4a", "wav", "aac"):
            return "audio"
        return "document"

    @staticmethod
    def _is_remote_media_url(path: str) -> bool:
        return path.startswith(("http://", "https://"))

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Telegram."""
        app = self._app_for_chat(msg.chat_id)
        if not app:
            logger.warning("Telegram bot not running")
            return

        # Only stop typing indicator and remove reaction for final responses
        if not msg.metadata.get("_progress", False):
            self._stop_typing(msg.chat_id)
            if reply_to_message_id := msg.metadata.get("message_id"):
                try:
                    await self._remove_reaction(msg.chat_id, int(reply_to_message_id))
                except ValueError:
                    pass

        try:
            chat_id = int(msg.chat_id)
        except ValueError:
            logger.error("Invalid chat_id: {}", msg.chat_id)
            return
        reply_to_message_id = msg.metadata.get("message_id")
        message_thread_id = msg.metadata.get("message_thread_id")
        if message_thread_id is None and reply_to_message_id is not None:
            message_thread_id = self._message_threads.get((msg.chat_id, reply_to_message_id))
        thread_kwargs = {}
        if message_thread_id is not None:
            thread_kwargs["message_thread_id"] = message_thread_id

        reply_params = None
        if self.config.reply_to_message:
            if reply_to_message_id:
                reply_params = ReplyParameters(
                    message_id=reply_to_message_id,
                    allow_sending_without_reply=True
                )

        # Send media files
        for media_path in (msg.media or []):
            try:
                media_type = self._get_media_type(media_path)
                sender = {
                    "photo": app.bot.send_photo,
                    "voice": app.bot.send_voice,
                    "audio": app.bot.send_audio,
                }.get(media_type, app.bot.send_document)
                param = "photo" if media_type == "photo" else media_type if media_type in ("voice", "audio") else "document"

                # Telegram Bot API accepts HTTP(S) URLs directly for media params.
                if self._is_remote_media_url(media_path):
                    ok, error = validate_url_target(media_path)
                    if not ok:
                        raise ValueError(f"unsafe media URL: {error}")
                    await self._call_with_retry(
                        sender,
                        chat_id=chat_id,
                        **{param: media_path},
                        reply_parameters=reply_params,
                        **thread_kwargs,
                    )
                    continue

                with open(media_path, "rb") as f:
                    await sender(
                        chat_id=chat_id,
                        **{param: f},
                        reply_parameters=reply_params,
                        **thread_kwargs,
                    )
            except Exception as e:
                filename = media_path.rsplit("/", 1)[-1]
                logger.error("Failed to send media {}: {}", media_path, e)
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"[Failed to send: {filename}]",
                    reply_parameters=reply_params,
                    **thread_kwargs,
                )

        # Send text content
        if msg.content and msg.content != "[empty message]":
            render_as_blockquote = bool(msg.metadata.get("_tool_hint"))
            for chunk in split_message(msg.content, TELEGRAM_MAX_MESSAGE_LEN):
                await self._send_text(
                    chat_id, chunk, reply_params, thread_kwargs,
                    render_as_blockquote=render_as_blockquote,
                    app=app,
                )

    async def _call_with_retry(self, fn, *args, **kwargs):
        """Call an async Telegram API function with retry on pool/network timeout and RetryAfter."""
        from telegram.error import RetryAfter
        
        for attempt in range(1, _SEND_MAX_RETRIES + 1):
            try:
                return await fn(*args, **kwargs)
            except TimedOut:
                if attempt == _SEND_MAX_RETRIES:
                    raise
                delay = _SEND_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    "Telegram timeout (attempt {}/{}), retrying in {:.1f}s",
                    attempt, _SEND_MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)
            except RetryAfter as e:
                if attempt == _SEND_MAX_RETRIES:
                    raise
                delay = float(e.retry_after)
                logger.warning(
                    "Telegram Flood Control (attempt {}/{}), retrying in {:.1f}s",
                    attempt, _SEND_MAX_RETRIES, delay,
                )
                await asyncio.sleep(delay)

    async def _send_text(
        self,
        chat_id: int,
        text: str,
        reply_params=None,
        thread_kwargs: dict | None = None,
        render_as_blockquote: bool = False,
        app: Any = None,
    ) -> None:
        """Send a plain text message with HTML fallback."""
        effective_app = app or self._app
        try:
            html = _tool_hint_to_telegram_blockquote(text) if render_as_blockquote else _markdown_to_telegram_html(text)
            await self._call_with_retry(
                effective_app.bot.send_message,
                chat_id=chat_id, text=html, parse_mode="HTML",
                reply_parameters=reply_params,
                **(thread_kwargs or {}),
            )
        except BadRequest as e:
            # Only fall back to plain text on actual HTML parse/format errors.
            # Network errors (TimedOut, NetworkError) should propagate immediately
            # to avoid doubling connection demand during pool exhaustion.
            logger.warning("HTML parse failed, falling back to plain text: {}", e)
            try:
                await self._call_with_retry(
                    effective_app.bot.send_message,
                    chat_id=chat_id,
                    text=text,
                    reply_parameters=reply_params,
                    **(thread_kwargs or {}),
                )
            except Exception as e2:
                logger.error("Error sending Telegram message: {}", e2)
                raise

    @staticmethod
    def _is_not_modified_error(exc: Exception) -> bool:
        return isinstance(exc, BadRequest) and "message is not modified" in str(exc).lower()

    async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None) -> None:
        """Progressive message editing: send on first delta, edit on subsequent ones."""
        app = self._app_for_chat(chat_id)
        if not app:
            return
        meta = metadata or {}
        int_chat_id = int(chat_id)
        stream_id = meta.get("_stream_id")

        if meta.get("_stream_end"):
            buf = self._stream_bufs.get(chat_id)
            if not buf or not buf.message_id or not buf.text:
                return
            if stream_id is not None and buf.stream_id is not None and buf.stream_id != stream_id:
                return
            self._stop_typing(chat_id)
            if reply_to_message_id := meta.get("message_id"):
                try:
                    await self._remove_reaction(chat_id, int(reply_to_message_id))
                except ValueError:
                    pass
            chunks = split_message(buf.text, TELEGRAM_MAX_MESSAGE_LEN)
            primary_text = chunks[0] if chunks else buf.text
            try:
                html = _markdown_to_telegram_html(primary_text)
                await self._call_with_retry(
                    app.bot.edit_message_text,
                    chat_id=int_chat_id, message_id=buf.message_id,
                    text=html, parse_mode="HTML",
                )
            except BadRequest as e:
                # Only fall back to plain text on actual HTML parse/format errors.
                # Network errors (TimedOut, NetworkError) should propagate immediately
                # to avoid doubling connection demand during pool exhaustion.
                if self._is_not_modified_error(e):
                    logger.debug("Final stream edit already applied for {}", chat_id)
                    self._stream_bufs.pop(chat_id, None)
                    return
                logger.debug("Final stream edit failed (HTML), trying plain: {}", e)
                try:
                    await self._call_with_retry(
                        app.bot.edit_message_text,
                        chat_id=int_chat_id, message_id=buf.message_id,
                        text=primary_text,
                    )
                except Exception as e2:
                    if self._is_not_modified_error(e2):
                        logger.debug("Final stream plain edit already applied for {}", chat_id)
                    else:
                        logger.warning("Final stream edit failed: {}", e2)
                        raise  # Let ChannelManager handle retry
            # If final content exceeds Telegram limit, keep the first chunk in
            # the edited stream message and send the rest as follow-up messages.
            for extra_chunk in chunks[1:]:
                await self._send_text(int_chat_id, extra_chunk, app=app)
            self._stream_bufs.pop(chat_id, None)
            return

        buf = self._stream_bufs.get(chat_id)
        if buf is None or (stream_id is not None and buf.stream_id is not None and buf.stream_id != stream_id):
            buf = _StreamBuf(stream_id=stream_id)
            self._stream_bufs[chat_id] = buf
        elif buf.stream_id is None:
            buf.stream_id = stream_id
        buf.text += delta

        if not buf.text.strip():
            return

        now = time.monotonic()
        thread_kwargs = {}
        if message_thread_id := meta.get("message_thread_id"):
            thread_kwargs["message_thread_id"] = message_thread_id
        if buf.message_id is None:
            try:
                sent = await self._call_with_retry(
                    app.bot.send_message,
                    chat_id=int_chat_id, text=buf.text,
                    **thread_kwargs,
                )
                buf.message_id = sent.message_id
                buf.last_edit = now
            except Exception as e:
                logger.warning("Stream initial send failed: {}", e)
                raise  # Let ChannelManager handle retry
        elif (now - buf.last_edit) >= self.config.stream_edit_interval:
            try:
                await self._call_with_retry(
                    app.bot.edit_message_text,
                    chat_id=int_chat_id, message_id=buf.message_id,
                    text=buf.text,
                )
                buf.last_edit = now
            except Exception as e:
                if self._is_not_modified_error(e):
                    buf.last_edit = now
                    return
                logger.warning("Stream edit failed: {}", e)
                raise  # Let ChannelManager handle retry

    async def _on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not update.message or not update.effective_user:
            return

        user = update.effective_user
        await update.message.reply_text(
            f"👋 Hi {user.first_name}! I'm nanobot.\n\n"
            "Send me a message and I'll respond!\n"
            "Type /help to see available commands."
        )

    async def _on_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command, bypassing ACL so all users can access it."""
        if not update.message:
            return
        await update.message.reply_text(build_help_text())

    @staticmethod
    def _sender_id(user) -> str:
        """Build sender_id with username for allowlist matching."""
        sid = str(user.id)
        return f"{sid}|{user.username}" if user.username else sid

    @staticmethod
    def _derive_topic_session_key(message, business_line: str | None = None) -> str | None:
        """Derive topic-scoped session key for Telegram chats.

        Includes business_line so each bot has an isolated session history
        even when the same user (same chat_id) talks to multiple bots.
        """
        message_thread_id = getattr(message, "message_thread_id", None)
        bl_suffix = f":{business_line}" if business_line else ""
        if message_thread_id is None:
            # In multi-bot mode, scope session to the specific business line.
            if business_line:
                return f"telegram:{message.chat_id}{bl_suffix}"
            return None
        return f"telegram:{message.chat_id}:topic:{message_thread_id}{bl_suffix}"

    @staticmethod
    def _build_message_metadata(message, user) -> dict:
        """Build common Telegram inbound metadata payload."""
        reply_to = getattr(message, "reply_to_message", None)
        return {
            "message_id": message.message_id,
            "user_id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "is_group": message.chat.type != "private",
            "message_thread_id": getattr(message, "message_thread_id", None),
            "is_forum": bool(getattr(message.chat, "is_forum", False)),
            "reply_to_message_id": getattr(reply_to, "message_id", None) if reply_to else None,
        }

    async def _extract_reply_context(self, message) -> str | None:
        """Extract text from the message being replied to, if any."""
        reply = getattr(message, "reply_to_message", None)
        if not reply:
            return None
        text = getattr(reply, "text", None) or getattr(reply, "caption", None) or ""
        if len(text) > TELEGRAM_REPLY_CONTEXT_MAX_LEN:
            text = text[:TELEGRAM_REPLY_CONTEXT_MAX_LEN] + "..."
            
        if not text:
            return None
            
        bot_id, _ = await self._ensure_bot_identity()
        reply_user = getattr(reply, "from_user", None)
        
        if bot_id and reply_user and getattr(reply_user, "id", None) == bot_id:
            return f"[Reply to bot: {text}]"
        elif reply_user and getattr(reply_user, "username", None):
            return f"[Reply to @{reply_user.username}: {text}]"
        elif reply_user and getattr(reply_user, "first_name", None):
            return f"[Reply to {reply_user.first_name}: {text}]"
        else:
            return f"[Reply to: {text}]"

    async def _download_message_media(
        self, msg, *, add_failure_content: bool = False
    ) -> tuple[list[str], list[str]]:
        """Download media from a message (current or reply). Returns (media_paths, content_parts)."""
        media_file = None
        media_type = None
        if getattr(msg, "photo", None):
            media_file = msg.photo[-1]
            media_type = "image"
        elif getattr(msg, "voice", None):
            media_file = msg.voice
            media_type = "voice"
        elif getattr(msg, "audio", None):
            media_file = msg.audio
            media_type = "audio"
        elif getattr(msg, "document", None):
            media_file = msg.document
            media_type = "file"
        elif getattr(msg, "video", None):
            media_file = msg.video
            media_type = "video"
        elif getattr(msg, "video_note", None):
            media_file = msg.video_note
            media_type = "video"
        elif getattr(msg, "animation", None):
            media_file = msg.animation
            media_type = "animation"
        app = self._ctx_app()
        if not media_file or not app:
            return [], []
        try:
            file = await app.bot.get_file(media_file.file_id)
            ext = self._get_extension(
                media_type,
                getattr(media_file, "mime_type", None),
                getattr(media_file, "file_name", None),
            )
            media_dir = get_media_dir("telegram")
            unique_id = getattr(media_file, "file_unique_id", media_file.file_id)
            file_path = media_dir / f"{unique_id}{ext}"
            await file.download_to_drive(str(file_path))
            path_str = str(file_path)
            if media_type in ("voice", "audio"):
                transcription = await self.transcribe_audio(file_path)
                if transcription:
                    logger.info("Transcribed {}: {}...", media_type, transcription[:50])
                    return [path_str], [f"[transcription: {transcription}]"]
                return [path_str], [f"[{media_type}: {path_str}]"]
            return [path_str], [f"[{media_type}: {path_str}]"]
        except Exception as e:
            logger.warning("Failed to download message media: {}", e)
            if add_failure_content:
                return [], [f"[{media_type}: download failed]"]
            return [], []

    async def _ensure_bot_identity(self) -> tuple[int | None, str | None]:
        """Load bot identity once per app and cache for mention/reply checks."""
        app = self._ctx_app()
        if not app:
            return None, None
        key = id(app)
        if key not in self._app_identities:
            bot_info = await app.bot.get_me()
            self._app_identities[key] = (
                getattr(bot_info, "id", None),
                getattr(bot_info, "username", None),
            )
        return self._app_identities[key]

    @staticmethod
    def _has_mention_entity(
        text: str,
        entities,
        bot_username: str,
        bot_id: int | None,
    ) -> bool:
        """Check Telegram mention entities against the bot username."""
        handle = f"@{bot_username}".lower()
        for entity in entities or []:
            entity_type = getattr(entity, "type", None)
            if entity_type == "text_mention":
                user = getattr(entity, "user", None)
                if user is not None and bot_id is not None and getattr(user, "id", None) == bot_id:
                    return True
                continue
            if entity_type != "mention":
                continue
            offset = getattr(entity, "offset", None)
            length = getattr(entity, "length", None)
            if offset is None or length is None:
                continue
            if text[offset : offset + length].lower() == handle:
                return True
        return handle in text.lower()

    async def _is_group_message_for_bot(self, message) -> bool:
        """Allow group messages when policy is open, @mentioned, or replying to the bot."""
        if message.chat.type == "private" or self.config.group_policy == "open":
            return True

        bot_id, bot_username = await self._ensure_bot_identity()
        if bot_username:
            text = message.text or ""
            caption = message.caption or ""
            if self._has_mention_entity(
                text,
                getattr(message, "entities", None),
                bot_username,
                bot_id,
            ):
                return True
            if self._has_mention_entity(
                caption,
                getattr(message, "caption_entities", None),
                bot_username,
                bot_id,
            ):
                return True

        reply_user = getattr(getattr(message, "reply_to_message", None), "from_user", None)
        return bool(bot_id and reply_user and reply_user.id == bot_id)

    def _remember_thread_context(self, message) -> None:
        """Cache Telegram thread context by chat/message id for follow-up replies."""
        message_thread_id = getattr(message, "message_thread_id", None)
        if message_thread_id is None:
            return
        key = (str(message.chat_id), message.message_id)
        self._message_threads[key] = message_thread_id
        if len(self._message_threads) > 1000:
            self._message_threads.pop(next(iter(self._message_threads)))

    async def _forward_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Forward slash commands to the bus for unified handling in AgentLoop."""
        if not update.message or not update.effective_user:
            return
        message = update.message
        user = update.effective_user
        self._remember_thread_context(message)

        # Track outbound routing for multi-bot (may already be set by wrapper, but safe to repeat).
        ctx = _current_bot_ctx.get()
        if ctx:
            self._chat_to_app[str(message.chat_id)] = ctx["app"]

        # Strip @bot_username suffix if present
        content = message.text or ""
        if content.startswith("/") and "@" in content:
            cmd_part, *rest = content.split(" ", 1)
            cmd_part = cmd_part.split("@")[0]
            content = f"{cmd_part} {rest[0]}" if rest else cmd_part
        content = self._normalize_telegram_command(content)

        await self._handle_message(
            sender_id=self._sender_id(user),
            chat_id=str(message.chat_id),
            content=content,
            metadata=self._build_message_metadata(message, user),
            session_key=self._derive_topic_session_key(message),
        )

    async def _on_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle incoming messages (text, photos, voice, documents)."""
        if not update.message or not update.effective_user:
            return

        message = update.message
        user = update.effective_user
        chat_id = message.chat_id
        sender_id = self._sender_id(user)
        self._remember_thread_context(message)

        # Store chat_id for replies
        self._chat_ids[sender_id] = chat_id

        if not await self._is_group_message_for_bot(message):
            return

        # Build content from text and/or media
        content_parts = []
        media_paths = []

        # Text content
        if message.text:
            content_parts.append(message.text)
        if message.caption:
            content_parts.append(message.caption)

        # Location content
        if message.location:
            lat = message.location.latitude
            lon = message.location.longitude
            content_parts.append(f"[location: {lat}, {lon}]")

        # Download current message media
        current_media_paths, current_media_parts = await self._download_message_media(
            message, add_failure_content=True
        )
        media_paths.extend(current_media_paths)
        content_parts.extend(current_media_parts)
        if current_media_paths:
            logger.debug("Downloaded message media to {}", current_media_paths[0])

        # Reply context: text and/or media from the replied-to message
        reply = getattr(message, "reply_to_message", None)
        if reply is not None:
            reply_ctx = await self._extract_reply_context(message)
            reply_media, reply_media_parts = await self._download_message_media(reply)
            if reply_media:
                media_paths = reply_media + media_paths
                logger.debug("Attached replied-to media: {}", reply_media[0])
            tag = reply_ctx or (f"[Reply to: {reply_media_parts[0]}]" if reply_media_parts else None)
            if tag:
                content_parts.insert(0, tag)
        content = "\n".join(content_parts) if content_parts else "[empty message]"

        logger.debug("Telegram message from {}: {}...", sender_id, content[:50])

        str_chat_id = str(chat_id)
        metadata = self._build_message_metadata(message, user)
        # Inject business_line from per-bot context (multi-bot mode).
        ctx = _current_bot_ctx.get()
        bot_business_line = ctx.get("business_line") if ctx else None
        if bot_business_line:
            metadata["business_line"] = bot_business_line
        session_key = self._derive_topic_session_key(message, business_line=bot_business_line)

        # Telegram media groups: buffer briefly, forward as one aggregated turn.
        if media_group_id := getattr(message, "media_group_id", None):
            key = f"{str_chat_id}:{media_group_id}"
            if key not in self._media_group_buffers:
                self._media_group_buffers[key] = {
                    "sender_id": sender_id, "chat_id": str_chat_id,
                    "contents": [], "media": [],
                    "metadata": metadata,
                    "session_key": session_key,
                }
                self._start_typing(str_chat_id)
                await self._add_reaction(str_chat_id, message.message_id, self.config.react_emoji)
            buf = self._media_group_buffers[key]
            if content and content != "[empty message]":
                buf["contents"].append(content)
            buf["media"].extend(media_paths)
            if key not in self._media_group_tasks:
                self._media_group_tasks[key] = asyncio.create_task(self._flush_media_group(key))
            return

        # Start typing indicator before processing
        self._start_typing(str_chat_id)
        await self._add_reaction(str_chat_id, message.message_id, self.config.react_emoji)

        # Forward to the message bus
        await self._handle_message(
            sender_id=sender_id,
            chat_id=str_chat_id,
            content=content,
            media=media_paths,
            metadata=metadata,
            session_key=session_key,
        )

    async def _flush_media_group(self, key: str) -> None:
        """Wait briefly, then forward buffered media-group as one turn."""
        try:
            await asyncio.sleep(0.6)
            if not (buf := self._media_group_buffers.pop(key, None)):
                return
            content = "\n".join(buf["contents"]) or "[empty message]"
            await self._handle_message(
                sender_id=buf["sender_id"], chat_id=buf["chat_id"],
                content=content, media=list(dict.fromkeys(buf["media"])),
                metadata=buf["metadata"],
                session_key=buf.get("session_key"),
            )
        finally:
            self._media_group_tasks.pop(key, None)

    def _start_typing(self, chat_id: str) -> None:
        """Start sending 'typing...' indicator for a chat."""
        # Cancel any existing typing task for this chat
        self._stop_typing(chat_id)
        self._typing_tasks[chat_id] = asyncio.create_task(self._typing_loop(chat_id))

    def _stop_typing(self, chat_id: str) -> None:
        """Stop the typing indicator for a chat."""
        task = self._typing_tasks.pop(chat_id, None)
        if task and not task.done():
            task.cancel()

    async def _add_reaction(self, chat_id: str, message_id: int, emoji: str) -> None:
        """Add emoji reaction to a message (best-effort, non-blocking)."""
        app = self._ctx_app()
        if not app or not emoji:
            return
        try:
            await app.bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=message_id,
                reaction=[ReactionTypeEmoji(emoji=emoji)],
            )
        except Exception as e:
            logger.debug("Telegram reaction failed: {}", e)

    async def _remove_reaction(self, chat_id: str, message_id: int) -> None:
        """Remove emoji reaction from a message (best-effort, non-blocking)."""
        # _remove_reaction is called both from send() (outbound) and send_delta() (outbound).
        # In the outbound path _current_bot_ctx is not set, so fall back to _app_for_chat.
        app = self._ctx_app() or self._app_for_chat(str(chat_id))
        if not app:
            return
        try:
            await app.bot.set_message_reaction(
                chat_id=int(chat_id),
                message_id=message_id,
                reaction=[],
            )
        except Exception as e:
            logger.debug("Telegram reaction removal failed: {}", e)

    async def _typing_loop(self, chat_id: str) -> None:
        """Repeatedly send 'typing' action until cancelled.

        asyncio.create_task copies the current context, so this task inherits
        the _current_bot_ctx set by the handler that started the typing indicator.
        """
        app = self._ctx_app()
        try:
            while app:
                await app.bot.send_chat_action(chat_id=int(chat_id), action="typing")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Typing indicator stopped for {}: {}", chat_id, e)

    @staticmethod
    def _format_telegram_error(exc: Exception) -> str:
        """Return a short, readable error summary for logs."""
        text = str(exc).strip()
        if text:
            return text
        if exc.__cause__ is not None:
            cause = exc.__cause__
            cause_text = str(cause).strip()
            if cause_text:
                return f"{exc.__class__.__name__} ({cause_text})"
            return f"{exc.__class__.__name__} ({cause.__class__.__name__})"
        return exc.__class__.__name__

    def _on_polling_error(self, exc: Exception) -> None:
        """Keep long-polling network failures to a single readable line."""
        summary = self._format_telegram_error(exc)
        if isinstance(exc, (NetworkError, TimedOut)):
            logger.warning("Telegram polling network issue: {}", summary)
        else:
            logger.error("Telegram polling error: {}", summary)

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log polling / handler errors instead of silently swallowing them."""
        summary = self._format_telegram_error(context.error)

        if isinstance(context.error, (NetworkError, TimedOut)):
            logger.warning("Telegram network issue: {}", summary)
        else:
            logger.error("Telegram error: {}", summary)

    def _get_extension(
        self,
        media_type: str,
        mime_type: str | None,
        filename: str | None = None,
    ) -> str:
        """Get file extension based on media type or original filename."""
        if mime_type:
            ext_map = {
                "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                "audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a",
            }
            if mime_type in ext_map:
                return ext_map[mime_type]

        type_map = {"image": ".jpg", "voice": ".ogg", "audio": ".mp3", "file": ""}
        if ext := type_map.get(media_type, ""):
            return ext

        if filename:
            from pathlib import Path

            return "".join(Path(filename).suffixes)

        return ""
