"""Vercel serverless Telegram webhook entrypoint for Nexa AI."""
import asyncio
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from nexa_ai import config, db
from nexa_ai.bot import start, help_command, clear, remember, memory, forget, models, process_message

log = logging.getLogger("nexa_ai.vercel")


def _build_application():
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
    return app


async def _process(payload):
    app = _build_application()
    await app.initialize()
    try:
        update = Update.de_json(payload, app.bot)
        await app.process_update(update)
    finally:
        await app.shutdown()


class handler(BaseHTTPRequestHandler):
    def _respond(self, status, message):
        body = json.dumps({"ok": status < 400, "message": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._respond(200, "Nexa AI Telegram webhook is online.")

    def do_POST(self):
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_OWNER_ID:
            self._respond(500, "Telegram configuration is incomplete.")
            return
        expected = config.TELEGRAM_WEBHOOK_SECRET
        received = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if expected and not hmac.compare_digest(expected, received):
            self._respond(403, "Invalid webhook secret.")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                self._respond(400, "Invalid request body size.")
                return
            payload = json.loads(self.rfile.read(length))
            db.init_db()
            asyncio.run(_process(payload))
            self._respond(200, "Update processed.")
        except Exception:
            log.exception("Failed to process Telegram webhook update")
            self._respond(500, "Could not process update.")

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)
