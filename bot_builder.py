import os
import sqlite3
import logging
import threading
import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes, filters
)

# =========================================================
# BOT BUILDER — single-file starter project
# Python 3.11+ | python-telegram-bot 21+
#
# ENVIRONMENT VARIABLES:
# BUILDER_TOKEN=...
# ADMIN_ID=123456789
# CARD_NUMBER=8600....
# CARD_HOLDER=YOUR NAME
# =========================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("botbuilder")

BUILDER_TOKEN = os.getenv("BUILDER_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CARD_NUMBER = os.getenv("CARD_NUMBER", "XXXX XXXX XXXX XXXX")
CARD_HOLDER = os.getenv("CARD_HOLDER", "Получатель")

DB_FILE = "botbuilder.db"

MIN_REFILL = 1_000
MAX_REFILL = 1_000_000

PRICE_STARS_PREMIUM = 50_000
PRICE_ACCOUNT_SERVICE = 25_000

(
    REFILL_AMOUNT,
    WAIT_RECEIPT,
    CONNECT_TOKEN,
    SUPPORT_MESSAGE,
) = range(4)

# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        balance INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS refills (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        amount INTEGER NOT NULL,
        receipt_file_id TEXT,
        receipt_type TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS bots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        bot_id INTEGER,
        username TEXT,
        first_name TEXT,
        token TEXT NOT NULL,
        bot_type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()


def ensure_user(tg_user):
    conn = db()
    conn.execute("""
        INSERT INTO users(user_id, username, full_name, balance, created_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username=excluded.username,
            full_name=excluded.full_name
    """, (
        tg_user.id,
        tg_user.username or "",
        tg_user.full_name or "",
        datetime.now().isoformat(),
    ))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = db()
    row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row


def add_balance(user_id, amount):
    conn = db()
    conn.execute(
        "UPDATE users SET balance = balance + ? WHERE user_id=?",
        (amount, user_id)
    )
    conn.commit()
    conn.close()


def get_bots(owner_id):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM bots WHERE owner_id=? ORDER BY id DESC",
        (owner_id,)
    ).fetchall()
    conn.close()
    return rows


def get_bot(bot_db_id):
    conn = db()
    row = conn.execute("SELECT * FROM bots WHERE id=?", (bot_db_id,)).fetchone()
    conn.close()
    return row


def update_bot_status(bot_db_id, status):
    conn = db()
    conn.execute("UPDATE bots SET status=? WHERE id=?", (status, bot_db_id))
    conn.commit()
    conn.close()


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu(user_id=None):
    rows = [
        [InlineKeyboardButton("🤖 Создать бота", callback_data="create")],
        [InlineKeyboardButton("📂 Мои боты", callback_data="mybots")],
        [
            InlineKeyboardButton("💳 Пополнить баланс", callback_data="refill"),
            InlineKeyboardButton("👤 Профиль", callback_data="profile"),
        ],
        [
            InlineKeyboardButton("🆘 Поддержка", callback_data="support"),
            InlineKeyboardButton("ℹ️ О сервисе", callback_data="about"),
        ],
    ]

    if user_id == ADMIN_ID:
        rows.append([InlineKeyboardButton("👑 Админ-панель", callback_data="admin")])

    return InlineKeyboardMarkup(rows)


def back_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏠 Главное меню", callback_data="home")]
    ])


# =========================================================
# BUILDER BOT HANDLERS
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update.effective_user)

    text = (
        "🤖 <b>BOT BUILDER</b>\n\n"
        "Создавайте и управляйте готовыми Telegram-ботами.\n\n"
        "💰 Пополняйте баланс\n"
        "⚡ Выбирайте шаблон\n"
        "🤖 Подключайте своего бота\n"
        "🛠 Управляйте им через Bot Builder"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(update.effective_user.id),
    )


async def home(query, context):
    await query.edit_message_text(
        "🏠 <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(query.from_user.id),
    )


async def profile(query):
    user = get_user(query.from_user.id)
    bots = get_bots(query.from_user.id)

    username = f"@{user['username']}" if user["username"] else "не указан"

    text = (
        "👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"👤 Username: {username}\n\n"
        f"💰 Баланс: <b>{user['balance']:,} UZS</b>\n"
        f"🤖 Создано ботов: <b>{len(bots)}</b>"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Пополнить баланс", callback_data="refill")],
        [InlineKeyboardButton("📂 Мои боты", callback_data="mybots")],
        [InlineKeyboardButton("🔙 Назад", callback_data="home")],
    ])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def refill_menu(query):
    text = (
        "💳 <b>Пополнение баланса</b>\n\n"
        f"Минимум: <b>{MIN_REFILL:,} UZS</b>\n"
        f"Максимум: <b>{MAX_REFILL:,} UZS</b>\n\n"
        "Выберите сумму или введите свою."
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1 000", callback_data="r_1000"),
            InlineKeyboardButton("5 000", callback_data="r_5000"),
        ],
        [
            InlineKeyboardButton("10 000", callback_data="r_10000"),
            InlineKeyboardButton("25 000", callback_data="r_25000"),
        ],
        [
            InlineKeyboardButton("50 000", callback_data="r_50000"),
            InlineKeyboardButton("100 000", callback_data="r_100000"),
        ],
        [InlineKeyboardButton("✏️ Своя сумма", callback_data="r_custom")],
        [InlineKeyboardButton("🔙 Назад", callback_data="home")],
    ])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def show_payment(query, context, amount):
    context.user_data["refill_amount"] = amount

    text = (
        "💳 <b>Оплата</b>\n\n"
        f"💰 Сумма: <b>{amount:,} UZS</b>\n\n"
        f"🏦 Карта:\n<code>{CARD_NUMBER}</code>\n\n"
        f"👤 Получатель: <b>{CARD_HOLDER}</b>\n\n"
        "⚠️ После оплаты отправьте <b>фото или файл чека</b> в этот чат."
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Я отправлю чек", callback_data="receipt_wait")],
        [InlineKeyboardButton("❌ Отменить", callback_data="refill")],
    ])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def refill_amount_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.replace(" ", "").replace(",", "")

    if not raw.isdigit():
        await update.message.reply_text("❌ Введите сумму цифрами.")
        return REFILL_AMOUNT

    amount = int(raw)

    if amount < MIN_REFILL or amount > MAX_REFILL:
        await update.message.reply_text(
            f"❌ Сумма должна быть от {MIN_REFILL:,} до {MAX_REFILL:,} UZS."
        )
        return REFILL_AMOUNT

    await show_payment_message(update, context, amount)
    return WAIT_RECEIPT


async def show_payment_message(update, context, amount):
    context.user_data["refill_amount"] = amount

    await update.message.reply_text(
        "💳 <b>Оплата</b>\n\n"
        f"💰 Сумма: <b>{amount:,} UZS</b>\n\n"
        f"🏦 Карта:\n<code>{CARD_NUMBER}</code>\n\n"
        f"👤 Получатель: <b>{CARD_HOLDER}</b>\n\n"
        "⚠️ После оплаты отправьте <b>фото или файл чека</b> сюда.",
        parse_mode=ParseMode.HTML,
    )


async def receipt_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    amount = context.user_data.get("refill_amount")

    if not amount:
        await update.message.reply_text("❌ Сначала выберите сумму пополнения.")
        return ConversationHandler.END

    receipt_file_id = None
    receipt_type = None

    if update.message.photo:
        receipt_file_id = update.message.photo[-1].file_id
        receipt_type = "photo"
    elif update.message.document:
        receipt_file_id = update.message.document.file_id
        receipt_type = "document"
    else:
        await update.message.reply_text("📸 Отправьте фото или файл чека.")
        return WAIT_RECEIPT

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO refills(user_id, amount, receipt_file_id, receipt_type, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
    """, (
        update.effective_user.id,
        amount,
        receipt_file_id,
        receipt_type,
        datetime.now().isoformat(),
    ))
    refill_id = cur.lastrowid
    conn.commit()
    conn.close()

    user = update.effective_user
    username = f"@{user.username}" if user.username else "нет username"

    admin_text = (
        "🔔 <b>НОВОЕ ПОПОЛНЕНИЕ</b>\n\n"
        f"👤 Пользователь: {username}\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"💰 Сумма: <b>{amount:,} UZS</b>\n"
        f"📋 Заявка: #{refill_id}"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve_{refill_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{refill_id}"),
        ]
    ])

    if ADMIN_ID:
        try:
            if receipt_type == "photo":
                await context.bot.send_photo(
                    ADMIN_ID,
                    receipt_file_id,
                    caption=admin_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
            else:
                await context.bot.send_document(
                    ADMIN_ID,
                    receipt_file_id,
                    caption=admin_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb,
                )
        except Exception:
            logger.exception("Could not send receipt to admin")

    await update.message.reply_text(
        "⏳ <b>Чек отправлен администрации!</b>\n\n"
        "Пожалуйста, дождитесь проверки.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_home(),
    )

    context.user_data.pop("refill_amount", None)
    return ConversationHandler.END


# =========================================================
# CREATE BOT
# =========================================================

async def create_menu(query):
    text = (
        "🤖 <b>Создание нового бота</b>\n\n"
        "Выберите готовый шаблон."
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"⭐💎 Stars + Premium Bot — {PRICE_STARS_PREMIUM:,} UZS",
            callback_data="buy_stars_premium"
        )],
        [InlineKeyboardButton(
            f"📱 Account Service Bot — {PRICE_ACCOUNT_SERVICE:,} UZS",
            callback_data="buy_account_service"
        )],
        [InlineKeyboardButton("🔙 Назад", callback_data="home")],
    ])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def buy_template(query, context, bot_type, price):
    user = get_user(query.from_user.id)

    if user["balance"] < price:
        missing = price - user["balance"]

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💳 Пополнить баланс", callback_data="refill")],
            [InlineKeyboardButton("🔙 Назад", callback_data="create")],
        ])

        await query.edit_message_text(
            "❌ <b>Недостаточно средств!</b>\n\n"
            f"💰 Стоимость: <b>{price:,} UZS</b>\n"
            f"💳 Ваш баланс: <b>{user['balance']:,} UZS</b>\n"
            f"📉 Не хватает: <b>{missing:,} UZS</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    context.user_data["selected_type"] = bot_type
    context.user_data["selected_price"] = price

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Продолжить", callback_data="confirm_buy")],
        [InlineKeyboardButton("❌ Отмена", callback_data="create")],
    ])

    await query.edit_message_text(
        "⚠️ <b>Подтверждение покупки</b>\n\n"
        f"📦 Шаблон: <b>{bot_type}</b>\n"
        f"💰 Цена: <b>{price:,} UZS</b>\n\n"
        "После подтверждения начнётся подключение вашего бота.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )


async def confirm_buy(query, context):
    bot_type = context.user_data.get("selected_type")
    price = context.user_data.get("selected_price")

    if not bot_type or not price:
        await query.answer("Ошибка. Выберите шаблон заново.", show_alert=True)
        return

    user = get_user(query.from_user.id)

    if user["balance"] < price:
        await query.answer("Недостаточно средств.", show_alert=True)
        return

    # Reserve/deduct the builder fee.
    # A production version should also add a purchase/refund table.
    add_balance(query.from_user.id, -price)

    context.user_data["awaiting_token"] = True

    await query.edit_message_text(
        "🔑 <b>Подключение бота</b>\n\n"
        "Создайте своего бота через BotFather и подключите его к этому сервису.\n\n"
        "После подключения отправьте данные доступа, необходимые для запуска выбранного бота.\n\n"
        "⚠️ Не отправляйте токены или другие секреты посторонним людям. "
        "В рабочей версии их следует хранить только в защищённом хранилище.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Отмена", callback_data="home")]
        ]),
    )


async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This starter implementation accepts a Bot API token to connect
    # a user-owned bot to the selected template.
    if not context.user_data.get("awaiting_token"):
        return

    token = update.message.text.strip()

    if ":" not in token or len(token) < 20:
        await update.message.reply_text(
            "❌ Это не похоже на корректные данные подключения. Попробуйте ещё раз."
        )
        return

    bot_type = context.user_data.get("selected_type")
    if not bot_type:
        await update.message.reply_text("❌ Сессия создания закончилась. Начните заново.")
        return

    await update.message.reply_text("⏳ Проверяем подключение к боту...")

    try:
        from telegram import Bot

        test_bot = Bot(token=token)
        me = await test_bot.get_me()

    except Exception:
        logger.exception("Token validation failed")
        await update.message.reply_text(
            "❌ Не удалось подключиться к этому боту.\n"
            "Проверьте данные и попробуйте снова."
        )
        return

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bots(owner_id, bot_id, username, first_name, token, bot_type, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'active', ?)
    """, (
        update.effective_user.id,
        me.id,
        me.username or "",
        me.first_name or "",
        token,
        bot_type,
        datetime.now().isoformat(),
    ))
    bot_db_id = cur.lastrowid
    conn.commit()
    conn.close()

    context.user_data.pop("awaiting_token", None)
    context.user_data.pop("selected_type", None)
    context.user_data.pop("selected_price", None)

    # Starts the template runtime in this process.
    started = await start_client_bot(bot_db_id, token, bot_type)

    if started:
        status = "🟢 Работает"
    else:
        status = "🟡 Подключён"

    username = f"@{me.username}" if me.username else me.first_name

    await update.message.reply_text(
        "🎉 <b>Бот успешно подключён!</b>\n\n"
        f"🤖 Бот: {username}\n"
        f"📦 Тип: <b>{bot_type}</b>\n"
        f"📊 Статус: <b>{status}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_home(),
    )


# =========================================================
# CLIENT BOT TEMPLATE RUNTIME
# =========================================================

client_apps = {}


def client_template_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Профиль", callback_data="client_profile")],
        [InlineKeyboardButton("🛒 Купить", callback_data="client_buy")],
        [InlineKeyboardButton("🆘 Поддержка", callback_data="client_support")],
    ])


async def client_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_type = context.application.bot_data.get("template_type", "Bot")

    if bot_type == "Stars + Premium Bot":
        text = (
            "⭐💎 <b>Добро пожаловать!</b>\n\n"
            "Здесь доступны Telegram Stars и Premium.\n\n"
            "Выберите нужный раздел."
        )
    else:
        text = (
            "📱 <b>Добро пожаловать!</b>\n\n"
            "Это ваш подключённый сервисный бот."
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=client_template_keyboard(),
    )


async def client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "client_profile":
        await q.edit_message_text(
            "👤 <b>Профиль</b>\n\n"
            f"🆔 <code>{q.from_user.id}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=client_template_keyboard(),
        )

    elif q.data == "client_buy":
        await q.edit_message_text(
            "🛒 <b>Заказ</b>\n\n"
            "Настройте каталог и способ выполнения заказов в следующей версии панели управления.",
            parse_mode=ParseMode.HTML,
            reply_markup=client_template_keyboard(),
        )

    else:
        await q.edit_message_text(
            "🆘 <b>Поддержка</b>\n\n"
            "Обратитесь к владельцу этого бота.",
            parse_mode=ParseMode.HTML,
            reply_markup=client_template_keyboard(),
        )


async def start_client_bot(bot_db_id, token, bot_type):
    if bot_db_id in client_apps:
        return True

    try:
        app = Application.builder().token(token).build()
        app.bot_data["template_type"] = bot_type

        app.add_handler(CommandHandler("start", client_start))
        app.add_handler(CallbackQueryHandler(client_callback))

        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=False)

        client_apps[bot_db_id] = app
        update_bot_status(bot_db_id, "active")

        logger.info("Client bot %s started", bot_db_id)
        return True

    except Exception:
        logger.exception("Failed to start client bot %s", bot_db_id)
        update_bot_status(bot_db_id, "error")
        return False


async def restore_client_bots():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM bots WHERE status='active'"
    ).fetchall()
    conn.close()

    for row in rows:
        await start_client_bot(row["id"], row["token"], row["bot_type"])


# =========================================================
# MY BOTS
# =========================================================

async def my_bots(query):
    bots = get_bots(query.from_user.id)

    if not bots:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Создать бота", callback_data="create")],
            [InlineKeyboardButton("🔙 Назад", callback_data="home")],
        ])
        await query.edit_message_text(
            "📂 <b>Мои боты</b>\n\nУ вас пока нет созданных ботов.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )
        return

    rows = []
    for b in bots:
        username = f"@{b['username']}" if b["username"] else b["first_name"]
        icon = "🟢" if b["status"] == "active" else "🟡"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {username}",
                callback_data=f"bot_{b['id']}"
            )
        ])

    rows.append([InlineKeyboardButton("➕ Создать нового", callback_data="create")])
    rows.append([InlineKeyboardButton("🔙 Назад", callback_data="home")])

    await query.edit_message_text(
        f"📂 <b>Ваши боты</b>\n\nВсего: <b>{len(bots)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def bot_panel(query, bot_id):
    bot = get_bot(bot_id)

    if not bot or bot["owner_id"] != query.from_user.id:
        await query.answer("Бот не найден.", show_alert=True)
        return

    username = f"@{bot['username']}" if bot["username"] else bot["first_name"]
    status = {
        "active": "🟢 Работает",
        "stopped": "🔴 Остановлен",
        "error": "🟠 Ошибка",
    }.get(bot["status"], bot["status"])

    text = (
        f"🤖 <b>{username}</b>\n\n"
        f"📦 Тип: <b>{bot['bot_type']}</b>\n"
        f"📊 Статус: <b>{status}</b>\n"
        f"📅 Создан: {bot['created_at'][:10]}"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Статус", callback_data=f"status_{bot_id}"),
            InlineKeyboardButton("🔄 Перезапустить", callback_data=f"restart_{bot_id}"),
        ],
        [InlineKeyboardButton("🔙 Мои боты", callback_data="mybots")],
    ])

    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def restart_client(query, bot_id):
    bot = get_bot(bot_id)

    if not bot or bot["owner_id"] != query.from_user.id:
        await query.answer("Нет доступа.", show_alert=True)
        return

    await query.answer("Перезапускаем...")

    old = client_apps.get(bot_id)
    if old:
        try:
            await old.updater.stop()
            await old.stop()
            await old.shutdown()
        except Exception:
            logger.exception("Error stopping client bot")
        client_apps.pop(bot_id, None)

    ok = await start_client_bot(bot_id, bot["token"], bot["bot_type"])

    await query.answer(
        "Бот перезапущен!" if ok else "Не удалось запустить.",
        show_alert=True,
    )


# =========================================================
# ADMIN REFILLS
# =========================================================

async def admin_refill_action(query, refill_id, approve):
    if query.from_user.id != ADMIN_ID:
        await query.answer("Нет доступа.", show_alert=True)
        return

    conn = db()
    refill = conn.execute(
        "SELECT * FROM refills WHERE id=?", (refill_id,)
    ).fetchone()

    if not refill:
        conn.close()
        await query.answer("Заявка не найдена.", show_alert=True)
        return

    if refill["status"] != "pending":
        conn.close()
        await query.answer("Эта заявка уже обработана.", show_alert=True)
        return

    if approve:
        conn.execute(
            "UPDATE refills SET status='approved' WHERE id=?",
            (refill_id,)
        )
        conn.execute(
            "UPDATE users SET balance=balance+? WHERE user_id=?",
            (refill["amount"], refill["user_id"])
        )
    else:
        conn.execute(
            "UPDATE refills SET status='rejected' WHERE id=?",
            (refill_id,)
        )

    conn.commit()
    conn.close()

    if approve:
        await context_send_user(
            query.get_bot(),
            refill["user_id"],
            f"✅ Ваше пополнение на {refill['amount']:,} UZS подтверждено!\n"
            f"💰 Средства начислены на баланс."
        )
        await query.edit_message_caption(
            caption=(query.message.caption or "") + "\n\n✅ <b>ОДОБРЕНО</b>",
            parse_mode=ParseMode.HTML,
        ) if query.message.caption else await query.edit_message_text("✅ ОДОБРЕНО")

    else:
        await context_send_user(
            query.get_bot(),
            refill["user_id"],
            f"❌ Ваше пополнение на {refill['amount']:,} UZS отклонено.\n"
            f"Если произошла ошибка — обратитесь в поддержку."
        )
        try:
            if query.message.caption:
                await query.edit_message_caption(
                    caption=query.message.caption + "\n\n❌ <b>ОТКЛОНЕНО</b>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await query.edit_message_text("❌ ОТКЛОНЕНО")
        except Exception:
            pass

    await query.answer("Готово.")


async def context_send_user(bot, user_id, text):
    try:
        await bot.send_message(user_id, text)
    except Exception:
        logger.exception("Could not notify user")


async def support_start(query, context):
    context.user_data["support_wait"] = True
    await query.edit_message_text(
        "🆘 <b>Поддержка</b>\n\n"
        "Напишите ваше сообщение следующим сообщением.",
        parse_mode=ParseMode.HTML,
        reply_markup=back_home(),
    )


async def support_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("support_wait"):
        return

    context.user_data.pop("support_wait", None)

    user = update.effective_user
    text = (
        "🆘 <b>НОВОЕ ОБРАЩЕНИЕ</b>\n\n"
        f"👤 @{user.username or 'нет username'}\n"
        f"🆔 <code>{user.id}</code>\n\n"
        f"💬 {update.message.text}"
    )

    if ADMIN_ID:
        await context.bot.send_message(
            ADMIN_ID,
            text,
            parse_mode=ParseMode.HTML,
        )

    await update.message.reply_text(
        "✅ Сообщение отправлено в поддержку.",
        reply_markup=back_home(),
    )


# =========================================================
# CALLBACK ROUTER
# =========================================================

async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "home":
        await home(query, context)

    elif data == "profile":
        await profile(query)

    elif data == "refill":
        await refill_menu(query)

    elif data.startswith("r_") and data != "r_custom":
        amount = int(data.split("_", 1)[1])
        await show_payment(query, context, amount)

    elif data == "r_custom":
        await query.edit_message_text(
            f"💰 Введите сумму от {MIN_REFILL:,} до {MAX_REFILL:,} UZS:"
        )
        context.user_data["refill_custom"] = True

    elif data == "receipt_wait":
        await query.edit_message_text(
            "📸 Теперь отправьте <b>фото или файл чека</b> следующим сообщением.",
            parse_mode=ParseMode.HTML,
        )
        context.user_data["waiting_receipt"] = True

    elif data == "create":
        await create_menu(query)

    elif data == "buy_stars_premium":
        await buy_template(query, context, "Stars + Premium Bot", PRICE_STARS_PREMIUM)

    elif data == "buy_account_service":
        await buy_template(query, context, "Account Service Bot", PRICE_ACCOUNT_SERVICE)

    elif data == "confirm_buy":
        await confirm_buy(query, context)

    elif data == "mybots":
        await my_bots(query)

    elif data.startswith("bot_"):
        await bot_panel(query, int(data.split("_", 1)[1]))

    elif data.startswith("restart_"):
        await restart_client(query, int(data.split("_", 1)[1]))

    elif data.startswith("approve_"):
        await admin_refill_action(query, int(data.split("_", 1)[1]), True)

    elif data.startswith("reject_"):
        await admin_refill_action(query, int(data.split("_", 1)[1]), False)

    elif data == "support":
        await support_start(query, context)

    elif data == "about":
        await query.edit_message_text(
            "ℹ️ <b>О сервисе</b>\n\n"
            "BOT BUILDER — сервис для подключения и управления готовыми шаблонами Telegram-ботов.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home(),
        )

    elif data == "admin":
        if query.from_user.id != ADMIN_ID:
            return
        conn = db()
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        bots_count = conn.execute("SELECT COUNT(*) FROM bots").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM refills WHERE status='pending'"
        ).fetchone()[0]
        conn.close()

        await query.edit_message_text(
            "👑 <b>Админ-панель</b>\n\n"
            f"👥 Пользователей: <b>{users}</b>\n"
            f"🤖 Ботов: <b>{bots_count}</b>\n"
            f"⏳ Пополнений на проверке: <b>{pending}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_home(),
        )


# =========================================================
# GENERAL TEXT / MEDIA ROUTER
# =========================================================

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_token"):
        await receive_token(update, context)
        return

    if context.user_data.get("refill_custom"):
        context.user_data.pop("refill_custom", None)
        await refill_amount_message(update, context)
        context.user_data["waiting_receipt"] = True
        return

    if context.user_data.get("support_wait"):
        await support_message(update, context)
        return


async def media_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("waiting_receipt"):
        return

    context.user_data.pop("waiting_receipt", None)
    await receipt_received(update, context)


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot Builder is running")

    def log_message(self, format, *args):
        return


def run_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# =========================================================
# MAIN
# =========================================================

async def post_init(app):
    await restore_client_bots()


def main():
    if not BUILDER_TOKEN:
        raise RuntimeError("BUILDER_TOKEN is not set")

    init_db()

    threading.Thread(target=run_health_server, daemon=True).start()

    app = (
        Application.builder()
        .token(BUILDER_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_router))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )
    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, media_router)
    )

    logger.info("BOT BUILDER STARTED")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
