import asyncio
import logging
import os
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from . import config, db
from .files import extract_attachment
from .providers import answer, configured_providers, ProviderError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nexa_ai")
MAX_TELEGRAM_MESSAGE = 3900

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
        response = f"{response}\n\n— {provider} · {model}"
        for start_idx in range(0, len(response), MAX_TELEGRAM_MESSAGE):
            await msg.reply_text(response[start_idx:start_idx + MAX_TELEGRAM_MESSAGE])
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
