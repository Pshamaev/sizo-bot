import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from supabase import create_client

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]

db = create_client(SUPABASE_URL, SUPABASE_KEY)

# Conversation states
PICK_SIZO, PICK_ACCESS, PICK_QUEUE, PICK_NOTE = range(4)

SIZOS = [
    ("СИЗО-1 (Матросская тишина)", "1"),
    ("СИЗО-2 (Бутырка)", "2"),
    ("СИЗО-4 (Медведь)", "4"),
    ("СИЗО-5 (Водники)", "5"),
    ("СИЗО-6 (Печатники)", "6"),
    ("СИЗО-7 (Капотня)", "7"),
    ("Другое", "other"),
]


def sizo_keyboard():
    rows = []
    for name, code in SIZOS:
        rows.append([InlineKeyboardButton(name, callback_data=f"sizo_{code}")])
    return InlineKeyboardMarkup(rows)


def access_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Пустили", callback_data="access_yes")],
        [InlineKeyboardButton("Не пустили", callback_data="access_no")],
    ])


def queue_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Электронная запись", callback_data="queue_electronic")],
        [InlineKeyboardButton("Живая очередь", callback_data="queue_live")],
        [InlineKeyboardButton("Оба варианта", callback_data="queue_both")],
    ])


def note_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Пропустить", callback_data="note_skip")],
    ])


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "СИЗО-бот. Команды:\n"
        "/report — оставить репорт после визита\n"
        "/status — последний репорт по СИЗО\n"
        "/help — справка"
    )


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("В какое СИЗО ходил?", reply_markup=sizo_keyboard())
    return PICK_SIZO


async def pick_sizo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    code = q.data.replace("sizo_", "")
    label = next((n for n, c in SIZOS if c == code), code)
    ctx.user_data["sizo_id"] = code
    ctx.user_data["sizo_label"] = label
    await q.edit_message_text(f"{label}\n\nПустили?", reply_markup=access_keyboard())
    return PICK_ACCESS


async def pick_access(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    val = "yes" if q.data == "access_yes" else "no"
    ctx.user_data["access_type"] = val
    if val == "no":
        await save_report(update, ctx)
        await q.edit_message_text("Репорт сохранён. Жаль что не пустили.")
        return ConversationHandler.END
    await q.edit_message_text(f"{ctx.user_data['sizo_label']}\n\nКакая очередь?", reply_markup=queue_keyboard())
    return PICK_QUEUE


async def pick_queue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["queue_type"] = q.data.replace("queue_", "")
    await q.edit_message_text(
        f"{ctx.user_data['sizo_label']}\n\nДобавь заметку (или пропусти):",
        reply_markup=note_keyboard()
    )
    return PICK_NOTE


async def pick_note_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["note"] = update.message.text
    await save_report(update, ctx)
    await update.message.reply_text("Репорт сохранён. Спасибо!")
    return ConversationHandler.END


async def pick_note_skip(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["note"] = None
    await save_report(update, ctx)
    await q.edit_message_text("Репорт сохранён. Спасибо!")
    return ConversationHandler.END


async def save_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = {
        "sizo_id": ctx.user_data.get("sizo_id"),
        "user_id": user.id,
        "username": user.username or user.full_name,
        "access_type": ctx.user_data.get("access_type"),
        "queue_type": ctx.user_data.get("queue_type"),
        "note": ctx.user_data.get("note"),
    }
    try:
        db.table("sizo_reports").insert(data).execute()
    except Exception as e:
        log.error(f"DB error: {e}")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("По какому СИЗО?", reply_markup=sizo_keyboard())


async def status_sizo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    code = q.data.replace("sizo_", "")
    label = next((n for n, c in SIZOS if c == code), code)
    try:
        res = (
            db.table("sizo_reports")
            .select("*")
            .eq("sizo_id", code)
            .eq("access_type", "yes")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            await q.edit_message_text(f"{label}: репортов пока нет.")
            return
        r = res.data[0]
        queue_map = {"electronic": "электронная запись", "live": "живая очередь", "both": "оба варианта"}
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        date_str = dt.strftime("%d.%m.%Y %H:%M")
        lines = [
            f"{label}",
            f"Последний репорт: {date_str}",
            f"Очередь: {queue_map.get(r.get('queue_type'), '?')}",
        ]
        if r.get("note"):
            lines.append(f"Заметка: {r['note']}")
        await q.edit_message_text("\n".join(lines))
    except Exception as e:
        log.error(f"Status error: {e}")
        await q.edit_message_text("Ошибка при получении данных.")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    report_conv = ConversationHandler(
        entry_points=[CommandHandler("report", cmd_report)],
        states={
            PICK_SIZO: [CallbackQueryHandler(pick_sizo, pattern="^sizo_")],
            PICK_ACCESS: [CallbackQueryHandler(pick_access, pattern="^access_")],
            PICK_QUEUE: [CallbackQueryHandler(pick_queue, pattern="^queue_")],
            PICK_NOTE: [
                CallbackQueryHandler(pick_note_skip, pattern="^note_skip"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, pick_note_text),
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    status_conv = ConversationHandler(
        entry_points=[CommandHandler("status", cmd_status)],
        states={
            PICK_SIZO: [CallbackQueryHandler(status_sizo, pattern="^sizo_")],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(report_conv)
    app.add_handler(status_conv)

    log.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
