import asyncio
import logging
import os
import re
from telegram import Update, MessageEntity
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from . import config, db
from .files import extract_attachment
from .providers import answer, configured_providers, ProviderError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nexa_ai")
MAX_TELEGRAM_MESSAGE = 3900
CODE_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)(?:```|$)", re.DOTALL)
LANGUAGE_ALIASES = {
    "js": "javascript", "ts": "typescript", "py": "python",
    "sh": "bash", "shell": "bash", "zsh": "bash", "yml": "yaml",
    "html": "html", "htm": "html", "jsx": "jsx", "tsx": "tsx",
    "env": "bash", "dotenv": "bash", "txt": "",
}

def _utf16_length(value: str) -> int:
    """Telegram entity offsets and lengths are measured in UTF-16 code units."""
    return len(value.encode("utf-16-le")) // 2

def _chunks(value: str, limit: int = MAX_TELEGRAM_MESSAGE):
    """Split text without breaking Unicode characters, preferring line boundaries."""
    remaining = value
    while remaining:
        units = 0
        end = 0
        last_break = 0
        for i, char in enumerate(remaining):
            char_units = _utf16_length(char)
            if units + char_units > limit:
                break
            units += char_units
            end = i + 1
            if char in "\n ":
                last_break = end
        if end == 0:  # defensive; a single Unicode code point should fit
            end = 1
        if end < len(remaining) and last_break > max(0, end - 500):
            end = last_break
        yield remaining[:end]
        remaining = remaining[end:]

def _looks_like_pasteable_block(block: str) -> bool:
    """Recognize common unfenced commands/config/data without wrapping normal prose."""
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    if not lines:
        return False
    if len(lines) == 1:
        line = lines[0]
        if line.startswith(("$ ", "> ", ">>> ", "curl ", "npm ", "npx ", "pnpm ",
                            "yarn ", "git ", "python ", "python3 ", "pip ", "pip3 ",
                            "cd ", "mkdir ", "cp ", "mv ", "cat ", "echo ", "export ",
                            "vercel ", "gh ", "docker ", "psql ", "uv ", "node ")):
            return True
        if re.match(r"^[A-Z][A-Z0-9_]{2,}=.{0,}$", line):
            return True
        if re.match(r"^https?://\S+$", line):
            return True
        if line.startswith(("BEGIN ", "SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE ", "ALTER ")):
            return True
        if ((line.startswith(("{", "[")) and line.endswith(("}", "]")) and (":" in line or "," in line))
                or line.startswith(("print(", "console.log(", "<div", "<html", "<!doctype", "SELECT "))
                or re.match(r"^(?:const|let|var|def|class|import|from)\s+", line)):
            return True
    if len(lines) >= 2:
        command_prefixes = ("npm ", "npx ", "pnpm ", "yarn ", "git ", "python ",
                            "python3 ", "pip ", "pip3 ", "cd ", "curl ", "vercel ",
                            "docker ", "export ", "mkdir ", "gh ")
        if all(line.startswith(command_prefixes) for line in lines):
            return True
        code_signals = sum(bool(re.match(
            r"^(?:[{}\[\],]|[A-Za-z_$][\w$.-]*\s*[:=]|"
            r"(?:const|let|var|function|import|export|from|def|class|return|if|for|while|"
            r"async|await|SELECT|INSERT|UPDATE|CREATE|ALTER|<!doctype|<\w+|"
            r"\$ |# |//|-- )|[A-Z][A-Z0-9_]{2,}=)", line, re.I
        )) for line in lines)
        if code_signals >= max(2, len(lines) // 2):
            return True
        if lines[0].startswith(("{", "[")) and lines[-1].endswith(("}", "]")):
            return True
    return False

def _response_segments(response: str):
    """Return (kind, text, language) segments; code is sent as Telegram PRE entities."""
    segments = []
    cursor = 0
    for match in CODE_FENCE_RE.finditer(response):
        if match.start() > cursor:
            segments.extend(_implicit_segments(response[cursor:match.start()]))
        language = match.group(1).strip().split()[0].lower() if match.group(1).strip() else ""
        language = LANGUAGE_ALIASES.get(language, language)
        code = match.group(2).strip("\n")
        if code:
            segments.append(("code", code, language))
        cursor = match.end()
    if cursor < len(response):
        segments.extend(_implicit_segments(response[cursor:]))
    if not segments and response:
        segments.append(("text", response, ""))
    return segments

def _implicit_segments(text: str):
    """Detect standalone copy/paste material, including plain-text prompts and form values."""
    parts = re.split(r"(\n\s*\n)", text)
    result = []
    copy_cue = re.compile(
        r"(?:copy(?: and paste)?|paste (?:this|the following)|prompt(?: to paste)?|"
        r"enter (?:this|the following)|run (?:this|the following)|use (?:this|the following)|"
        r"submit (?:this|the following)|send (?:this|the following)|message to send|"
        r"put this into|add this to|replace (?:it|this|the file) with)\s*[:：]?\s*$",
        re.I,
    )
    force_next = False
    for part in parts:
        if not part:
            continue
        if part.isspace():
            if result and result[-1][0] == "text":
                result[-1] = ("text", result[-1][1] + part, "")
            continue
        stripped = part.strip()
        if force_next or _looks_like_pasteable_block(part):
            result.append(("code", stripped, _guess_language(part)))
            force_next = False
        else:
            result.append(("text", part, ""))
            force_next = bool(
                copy_cue.search(stripped)
                or re.search(
                    r"\b(?:prompt|copy(?: and paste)?|paste|enter|run|submit|send)\b"
                    r".{0,100}\b(?:into|below|following|this|field|editor|terminal|chat|"
                    r"claude|chatgpt|another ai)\b",
                    stripped,
                    re.I,
                )
            )
    return result

def _guess_language(block: str) -> str:
    value = block.strip().lower()
    if value.startswith(("npm ", "npx ", "pnpm ", "yarn ", "git ", "python ", "pip ",
                         "pip3 ", "cd ", "mkdir ", "curl ", "vercel ", "docker ", "export ")):
        return "bash"
    if value.startswith(("{", "[")) and ('":' in value or "':" in value):
        return "json"
    if "<!doctype" in value or re.search(r"</?[a-z][^>]*>", value):
        return "html"
    if re.search(r"\b(select|insert into|update\s+\w+|create table)\b", value):
        return "sql"
    if re.search(r"\b(const|let|var|function|console\.log)\b", value):
        return "javascript"
    if re.search(r"^def |^import |^from |^class ", value, re.M):
        return "python"
    if re.match(r"^[A-Z][A-Z0-9_]{2,}=", block.strip()):
        return "bash"
    return ""

async def reply_formatted(message, response: str):
    """Send assistant output as plain prose plus native Telegram copyable code blocks."""
    for kind, value, language in _response_segments(response):
        if kind == "text":
            for chunk in _chunks(value):
                if chunk:
                    await message.reply_text(chunk)
            continue
        for chunk in _chunks(value):
            if not chunk:
                continue
            entity = MessageEntity(
                type=MessageEntity.PRE,
                offset=0,
                length=_utf16_length(chunk),
                language=language or None,
            )
            await message.reply_text(chunk, entities=[entity])

def is_owner(update: Update) -> bool:
    return bool(update.effective_user and config.TELEGRAM_OWNER_ID
                and str(update.effective_user.id) == config.TELEGRAM_OWNER_ID)

async def guard(update: Update) -> bool:
    if not is_owner(update):
        if update.effective_message:
            await update.effective_message.reply_text("🔒 Nexa AI is a private bot. You are not authorized to use it.")
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    await update.message.reply_text(
        "👋 Welcome to Nexa AI!\n\n"
        "Chat naturally, ask follow-up questions, or attach an image, PDF, document, spreadsheet, code file, or ZIP project.\n\n"
        "Provider priority: Clean APIs → Gemini → Groq → OpenRouter.\n\n"
        "Commands:\n/new — new conversation\n/memory — show saved memories\n/remember <fact> — save a memory\n/forget — clear saved memories\n/clear — clear conversation history\n/models — show configured providers\n/help — show help"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    await update.message.reply_text(
        "Send any message to chat with Nexa AI. Attach files to analyze them.\n\n"
        "/new or /clear — clear current chat history\n"
        "/remember <fact> — save a fact for future chats\n"
        "/memory — view saved facts\n/forget — delete saved facts\n/models — list configured providers\n\n"
        "The bot is private and only the configured Telegram owner ID is allowed."
    )

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    db.clear_history(str(update.effective_user.id))
    await update.message.reply_text("🧹 Conversation history cleared. Let's start fresh.")

async def remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    fact = " ".join(context.args).strip()
    if not fact:
        await update.message.reply_text("Usage: /remember the fact you want Nexa AI to remember")
        return
    db.save_memory(str(update.effective_user.id), fact)
    await update.message.reply_text("🧠 Saved to your private memory.")

async def memory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    facts = db.get_memories(str(update.effective_user.id))
    await update.message.reply_text("🧠 Your saved memories:\n\n" + ("\n".join(f"• {x}" for x in facts) if facts else "No saved memories yet."))

async def forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    db.clear_memories(str(update.effective_user.id))
    await update.message.reply_text("Your saved memories have been cleared.")

async def models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    providers = configured_providers()
    if not providers:
        await update.message.reply_text("No provider API keys are configured yet.")
        return
    await update.message.reply_text("🔀 Automatic provider priority:\n" + "\n".join(
        f"{i}. {name}" for i, (name, _) in enumerate(providers, 1)) +
        "\n\nThe bot tries configured providers in this order when a request fails. Model IDs are set in environment variables.")

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update): return
    msg = update.effective_message
    user_id = str(update.effective_user.id)
    text = (msg.caption or msg.text or "").strip()
    attachment = None
    try:
        if msg.photo:
            tg_file = await context.bot.get_file(msg.photo[-1].file_id)
            data = bytes(await tg_file.download_as_bytearray())
            attachment = extract_attachment("photo.jpg", data, "image/jpeg")
            if not text: text = "Please analyze this image."
        elif msg.document:
            doc = msg.document
            if doc.file_size and doc.file_size > config.MAX_ATTACHMENT_MB * 1024 * 1024:
                await msg.reply_text(f"File too large. Limit is {config.MAX_ATTACHMENT_MB} MB.")
                return
            tg_file = await context.bot.get_file(doc.file_id)
            data = bytes(await tg_file.download_as_bytearray())
            attachment = extract_attachment(doc.file_name or "attachment", data, doc.mime_type or "")
            if attachment["kind"] == "text":
                text = (text + "\n\n" if text else "") + attachment["text"]
            elif not text:
                text = attachment.get("prompt_text", "Please analyze this attachment.")
        elif not text:
            return

        history = db.get_history(user_id)
        memories = db.get_memories(user_id)
        if memories:
            memory_context = "Relevant saved user memories (use only when helpful):\n" + "\n".join(f"- {m}" for m in memories)
            history = [{"role": "assistant", "content": memory_context}] + history

        await context.bot.send_chat_action(chat_id=msg.chat_id, action=ChatAction.TYPING)
        response, provider, model = await answer(history, text, attachment)
        db.add_message(user_id, "user", text if not attachment else (text[:5000] + f"\n[Attachment: {getattr(msg.document, 'file_name', 'photo')}]"))
        db.add_message(user_id, "assistant", response)
        # Keep prose readable while sending copy/paste material as native Telegram
        # PRE entities (Telegram's built-in Copy UI in supported clients).
        await reply_formatted(msg, response)
        await msg.reply_text(f"— {provider} · {model}")
    except ProviderError as exc:
        log.warning("All providers failed for owner %s: %s", user_id, exc)
        await msg.reply_text("⚠️ I couldn't get a response from the configured providers.\n\n" + str(exc)[:3000])
    except Exception as exc:
        log.exception("Message processing failed")
        await msg.reply_text(f"⚠️ I couldn't process that message or attachment: {str(exc)[:800]}")

def main():
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN. Copy .env.example to .env and configure it.")
    if not config.TELEGRAM_OWNER_ID:
        raise SystemExit("Missing TELEGRAM_OWNER_ID. Set your numeric Telegram user ID.")
    db.init_db()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", clear))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("remember", remember))
    app.add_handler(CommandHandler("memory", memory))
    app.add_handler(CommandHandler("forget", forget))
    app.add_handler(CommandHandler("models", models))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.PHOTO | filters.Document.ALL, process_message))
    log.info("Starting Nexa AI Telegram bot")
    app.run_polling(drop_pending_updates=False)

if __name__ == "__main__":
    main()
