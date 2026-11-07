import logging
import io
import os
import re
import threading
from html import escape

import requests
import telebot
from PIL import Image, ImageDraw, ImageFilter
from telebot import types

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_URL = "https://app-creator-x.ueu.workers.dev/api/v1/build"
BANNER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "apk_converter_banner.png"
)

MAX_HTML_SIZE = 10 * 1024 * 1024
MAX_ZIP_SIZE = 10 * 1024 * 1024
MAX_ICON_SIZE = 6 * 1024 * 1024
MAX_ICON_PIXELS = 4_000_000
MAX_TOTAL_BODY = 16_842_752
MAX_TOTAL_FILES = MAX_TOTAL_BODY - 64 * 1024
MAX_APP_NAME_UTF16 = 80
MAX_APP_NAME_UTF8 = 120

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("apk_converter_bot")

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise SystemExit("Set a valid BOT_TOKEN environment variable.")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
sessions = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BEAUTIFUL BOTTOM KEYBOARDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def kb_main_menu():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    kb.add(
        types.KeyboardButton("🚀 Create APK"),
        types.KeyboardButton("📘 How it works")
    )
    kb.add(
        types.KeyboardButton("ℹ️ About")
    )
    return kb


def kb_cancel():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=1
    )
    kb.add(
        types.KeyboardButton("✖️ Cancel")
    )
    return kb


def kb_back():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=1
    )
    kb.add(
        types.KeyboardButton("⬅️ Back to menu")
    )
    return kb


def kb_confirm():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2
    )
    kb.add(
        types.KeyboardButton("✅ Build APK"),
        types.KeyboardButton("✏️ Rename")
    )
    kb.add(
        types.KeyboardButton("✖️ Cancel")
    )
    return kb


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BANNER GENERATOR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def ensure_banner():
    if os.path.exists(BANNER_PATH):
        return

    w, h = 1200, 675
    im = Image.new("RGB", (w, h), "#10162d")
    px = im.load()

    for y in range(h):
        for x in range(w):
            t = (x / w + y / h) / 2
            px[x, y] = (
                int(15 + 18 * t),
                int(22 + 26 * t),
                int(53 + 55 * t)
            )

    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)

    gd.ellipse(
        (760, -160, 1320, 400),
        fill=(101, 88, 255, 95)
    )
    gd.ellipse(
        (-180, 410, 430, 1030),
        fill=(0, 207, 191, 55)
    )

    glow = glow.filter(ImageFilter.GaussianBlur(70))
    im = Image.alpha_composite(im.convert("RGBA"), glow)

    d = ImageDraw.Draw(im)

    d.rounded_rectangle(
        (435, 85, 765, 590),
        radius=42,
        fill="#202a4b",
        outline="#6976b6",
        width=7
    )

    d.rounded_rectangle(
        (458, 132, 742, 535),
        radius=14,
        fill="#0c1329"
    )

    d.rounded_rectangle(
        (555, 101, 645, 119),
        radius=9,
        fill="#6976b6"
    )

    d.ellipse(
        (579, 551, 621, 593),
        fill="#6976b6"
    )

    d.rounded_rectangle(
        (535, 230, 665, 390),
        radius=22,
        fill="#665cff"
    )

    d.polygon(
        [(600, 198), (646, 240), (554, 240)],
        fill="#8d86ff"
    )

    d.line((574, 219, 560, 200), fill="#8d86ff", width=8)
    d.line((626, 219, 640, 200), fill="#8d86ff", width=8)

    d.ellipse((568, 278, 584, 294), fill="white")
    d.ellipse((616, 278, 632, 294), fill="white")

    d.rounded_rectangle(
        (570, 330, 630, 347),
        radius=8,
        fill="#b9b5ff"
    )

    im.convert("RGB").save(
        BANNER_PATH,
        "PNG",
        optimize=True
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BASIC FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def reset(chat_id):
    sessions.pop(chat_id, None)


def safe_name(value):
    return escape(value or "")


def progress(step, label):
    labels = [
        "App name",
        "Website",
        "Icon",
        "Review",
        "Build"
    ]

    cells = [
        "✅" if i < step else
        ("🔵" if i == step else "⚪")
        for i in range(5)
    ]

    return (
        f"<b>STEP {step + 1}/5 — {label}</b>\n"
        f"{'  '.join(cells)}\n\n"
    )


def validate_app_name(value):
    value = (value or "").strip()

    if not value:
        return "Please send a non-empty app name."

    utf16_units = len(value.encode("utf-16-le")) // 2
    utf8_bytes = len(value.encode("utf-8"))

    if utf16_units > MAX_APP_NAME_UTF16:
        return f"Name too long ({utf16_units}/80 UTF-16 units)."

    if utf8_bytes > MAX_APP_NAME_UTF8:
        return f"Name too large ({utf8_bytes}/120 UTF-8 bytes)."

    if any(
        ord(char) < 32 or 127 <= ord(char) <= 159
        for char in value
    ):
        return "Control characters are not allowed."

    return None


def validate_icon(content):
    if not content:
        return "Icon file is empty."

    try:
        with Image.open(io.BytesIO(content)) as image:

            if image.format not in ("PNG", "JPEG"):
                return "Only PNG and JPEG icons are supported."

            width, height = image.size

            if width * height > MAX_ICON_PIXELS:
                return "Maximum icon resolution is 4 megapixels."

            if image.format == "PNG" and image.info.get("interlace"):
                return "Interlaced PNG is not supported."

    except Exception:
        return "Invalid PNG or JPEG image."

    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HOME MENU
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_home(chat_id, intro=True):

    text = (
        "<b>💎 APK CONVERTER STUDIO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "🌐 <b>WEBSITE → ANDROID APK</b>\n\n"
        "Convert your HTML website or ZIP project "
        "into an Android application.\n\n"
        "🚀 Fast Build\n"
        "🛡️ File Validation\n"
        "📦 APK Delivery\n\n"
        "<i>Simple • Secure • Professional</i>\n\n"
        "👇 <b>Select an option below</b>"
        if intro else
        "<b>💎 APK CONVERTER STUDIO</b>\n\n"
        "👇 Choose an option below."
    )

    if os.path.exists(BANNER_PATH):
        with open(BANNER_PATH, "rb") as photo:
            bot.send_photo(
                chat_id,
                photo,
                caption=text,
                reply_markup=kb_main_menu()
            )
    else:
        bot.send_message(
            chat_id,
            text,
            reply_markup=kb_main_menu()
        )


def edit_ui_message(message, text, markup=None):
    if not message:
        return

    try:
        if message.content_type == "photo":
            bot.edit_message_caption(
                text,
                message.chat.id,
                message.message_id,
                reply_markup=markup
            )
        else:
            bot.edit_message_text(
                text,
                message.chat.id,
                message.message_id,
                reply_markup=markup
            )
    except Exception as exc:
        logger.debug("UI update failed: %s", exc)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FLOW SYSTEM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def flow_next(chat_id, text, handler, *args):
    msg = bot.send_message(
        chat_id,
        text,
        reply_markup=kb_cancel()
    )

    bot.register_next_step_handler(
        msg,
        handler,
        *args
    )

    sessions[chat_id]["ui_message"] = msg
    return msg


def start_flow(chat_id):
    sessions[chat_id] = {}

    text = (
        progress(0, "App name") +
        "📛 <b>ENTER YOUR APP NAME</b>\n\n"
        "Send the name of your Android application.\n\n"
        "Maximum: 80 UTF-16 units / 120 UTF-8 bytes."
    )

    flow_next(
        chat_id,
        text,
        step_app_name
    )


def cancel_flow(chat_id):
    reset(chat_id)

    bot.send_message(
        chat_id,
        "✖️ <b>PROCESS CANCELLED</b>\n\n"
        "Your current operation has been cancelled.\n"
        "Nothing was built.",
        reply_markup=kb_main_menu()
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 1: APP NAME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def step_app_name(message, rename=False):
    chat_id = message.chat.id

    if chat_id not in sessions:
        return

    if message.text == "✖️ Cancel":
        cancel_flow(chat_id)
        return

    name = (message.text or "").strip()

    error = validate_app_name(name)

    if error:
        flow_next(
            chat_id,
            progress(0, "App name") +
            "⚠️ <b>INVALID APP NAME</b>\n\n" +
            escape(error) +
            "\n\nPlease try again:",
            step_app_name,
            rename
        )
        return

    sessions[chat_id]["app_name"] = name

    if rename:
        show_confirmation(chat_id)
        return

    flow_next(
        chat_id,
        progress(1, "Website") +
        f"✅ Name saved: <b>{safe_name(name)}</b>\n\n"
        "📄 <b>UPLOAD YOUR WEBSITE</b>\n\n"
        "Send an HTML, HTM or ZIP document.",
        step_html_file
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 2: WEBSITE FILE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def step_html_file(message):
    chat_id = message.chat.id

    if chat_id not in sessions:
        return

    if message.text == "✖️ Cancel":
        cancel_flow(chat_id)
        return

    if not message.document:
        flow_next(
            chat_id,
            progress(1, "Website") +
            "⚠️ <b>FILE REQUIRED</b>\n\n"
            "Upload your website as an HTML, HTM or ZIP document.",
            step_html_file
        )
        return

    doc = message.document

    filename = doc.file_name or "index.html"

    ext = (
        filename.rsplit(".", 1)[-1].lower()
        if "." in filename else ""
    )

    if ext not in ("html", "htm", "zip"):
        flow_next(
            chat_id,
            "⚠️ <b>UNSUPPORTED FILE</b>\n\n"
            "Only .html, .htm and .zip files are allowed.",
            step_html_file
        )
        return

    limit = MAX_ZIP_SIZE if ext == "zip" else MAX_HTML_SIZE

    if doc.file_size and doc.file_size > limit:
        flow_next(
            chat_id,
            "⚠️ <b>FILE TOO LARGE</b>\n\n"
            f"Maximum size: {limit:,} bytes.",
            step_html_file
        )
        return

    try:
        info = bot.get_file(doc.file_id)
        content = bot.download_file(info.file_path)

    except Exception as exc:
        logger.exception("Website download failed")

        flow_next(
            chat_id,
            "⚠️ <b>DOWNLOAD FAILED</b>\n\n"
            f"{escape(str(exc))}",
            step_html_file
        )
        return

    sessions[chat_id]["html"] = (
        filename,
        content
    )

    flow_next(
        chat_id,
        progress(2, "Icon") +
        f"✅ Website received: <code>{escape(filename)}</code>\n\n"
        "🖼️ <b>UPLOAD YOUR APP ICON</b>\n\n"
        "Send a PNG or JPEG image.\n"
        "Maximum: 6 MiB / 4 megapixels.",
        step_icon_file
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 3: APP ICON
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def step_icon_file(message):
    chat_id = message.chat.id

    if chat_id not in sessions:
        return

    if message.text == "✖️ Cancel":
        cancel_flow(chat_id)
        return

    filename = "icon.jpg"

    if message.document:

        doc = message.document

        filename = doc.file_name or "icon.png"

        ext = (
            filename.rsplit(".", 1)[-1].lower()
            if "." in filename else ""
        )

        if ext not in ("png", "jpg", "jpeg"):
            flow_next(
                chat_id,
                "⚠️ <b>UNSUPPORTED ICON</b>\n\n"
                "Send a PNG or JPEG image.",
                step_icon_file
            )
            return

        if doc.file_size and doc.file_size > MAX_ICON_SIZE:
            flow_next(
                chat_id,
                "⚠️ <b>ICON TOO LARGE</b>\n\n"
                "Maximum size is 6 MiB.",
                step_icon_file
            )
            return

        file_id = doc.file_id

    elif message.photo:

        file_id = message.photo[-1].file_id
        filename = "icon.jpg"

    else:

        flow_next(
            chat_id,
            "⚠️ <b>ICON REQUIRED</b>\n\n"
            "Please send a PNG or JPEG image.",
            step_icon_file
        )
        return

    try:
        info = bot.get_file(file_id)
        content = bot.download_file(info.file_path)

    except Exception as exc:
        logger.exception("Icon download failed")

        flow_next(
            chat_id,
            "⚠️ <b>ICON DOWNLOAD FAILED</b>\n\n"
            f"{escape(str(exc))}",
            step_icon_file
        )
        return

    if len(content) > MAX_ICON_SIZE:
        flow_next(
            chat_id,
            "⚠️ <b>ICON TOO LARGE</b>\n\n"
            "Maximum size is 6 MiB.",
            step_icon_file
        )
        return

    error = validate_icon(content)

    if error:
        flow_next(
            chat_id,
            "⚠️ <b>INVALID ICON</b>\n\n"
            f"{escape(error)}",
            step_icon_file
        )
        return

    html_size = len(sessions[chat_id]["html"][1])

    if html_size + len(content) > MAX_TOTAL_FILES:
        cancel_flow(chat_id)

        bot.send_message(
            chat_id,
            "⚠️ <b>FILES TOO LARGE TOGETHER</b>\n\n"
            "Website and icon exceed the API request limit.",
            reply_markup=kb_main_menu()
        )
        return

    sessions[chat_id]["icon"] = (
        filename,
        content
    )

    show_confirmation(chat_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 4: REVIEW
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def show_confirmation(chat_id):

    session = sessions[chat_id]

    html_name, html_bytes = session["html"]
    icon_name, icon_bytes = session["icon"]

    text = (
        progress(3, "Review") +
        "📋 <b>BUILD REVIEW</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📛 <b>App Name:</b> {safe_name(session['app_name'])}\n\n"
        f"📄 <b>Website:</b> <code>{escape(html_name)}</code>\n"
        f"📦 <b>Website Size:</b> {len(html_bytes):,} bytes\n\n"
        f"🖼️ <b>Icon:</b> <code>{escape(icon_name)}</code>\n"
        f"📦 <b>Icon Size:</b> {len(icon_bytes):,} bytes\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Everything is ready.\n\n"
        "Choose an option below:"
    )

    msg = bot.send_message(
        chat_id,
        text,
        reply_markup=kb_confirm()
    )

    session["ui_message"] = msg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BUILD ANIMATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def animate_build(message, stop_event):

    frames = ["◐", "◓", "◑", "◒"]

    stages = [
        ("Uploading website", "📤", 25),
        ("Preparing WebView", "🧩", 45),
        ("Compiling Android package", "⚙️", 70),
        ("Signing test build", "🔐", 88),
        ("Finalizing APK", "📦", 96)
    ]

    index = 0

    while not stop_event.is_set():

        label, icon, percent = stages[index % len(stages)]

        filled = max(1, percent // 10)

        bar = (
            "🟦" * filled +
            "▫️" * (10 - filled)
        )

        text = (
            f"<b>{frames[index % len(frames)]} APK CONVERTER STUDIO</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>⚙️ BUILD IN PROGRESS</b>\n\n"
            f"{icon} <b>{label}</b>\n\n"
            f"{bar} <b>{percent}%</b>\n\n"
            "☕ Please wait while the build service works…\n\n"
            "<i>Your files are being processed.</i>"
        )

        edit_ui_message(message, text)

        index += 1
        stop_event.wait(1.5)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API ERROR HANDLER
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def api_error_message(resp):

    try:
        payload = resp.json()
        detail = payload.get("error", resp.text)

    except ValueError:
        detail = resp.text or f"HTTP {resp.status_code}"

    status_help = {
        400: "Please check the app name and uploaded files.",
        413: "The complete request is too large.",
        415: "The upload format was rejected.",
        429: "Please wait a minute and retry.",
        500: "The build service failed unexpectedly."
    }.get(
        resp.status_code,
        "Please try again later."
    )

    return (
        f"{escape(str(detail))}\n\n"
        f"<i>{status_help}</i>"
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STEP 5: BUILD APK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def build_apk(chat_id):

    session = sessions.get(chat_id)

    if not session:
        return

    ui_message = session.get("ui_message")

    stop_event = threading.Event()

    animation_thread = None

    if ui_message:

        animation_thread = threading.Thread(
            target=animate_build,
            args=(ui_message, stop_event),
            daemon=True
        )

        animation_thread.start()

    app_name = session["app_name"]

    html_name, html_bytes = session["html"]
    icon_name, icon_bytes = session["icon"]

    html_mime = (
        "application/zip"
        if html_name.lower().endswith(".zip")
        else "text/html"
    )

    icon_mime = (
        "image/png"
        if icon_name.lower().endswith("png")
        else "image/jpeg"
    )

    files = {
        "html": (
            html_name,
            html_bytes,
            html_mime
        ),
        "icon": (
            icon_name,
            icon_bytes,
            icon_mime
        )
    }

    try:

        resp = requests.post(
            API_URL,
            data={"app_name": app_name},
            files=files,
            timeout=120
        )

        stop_event.set()

        if animation_thread:
            animation_thread.join(timeout=2)

        content_type = resp.headers.get(
            "Content-Type", ""
        )

        if (
            resp.status_code == 200
            and "application/vnd.android.package-archive"
            in content_type
        ):

            if ui_message:
                edit_ui_message(
                    ui_message,
                    "✅ <b>APK BUILD COMPLETE</b>\n\n"
                    "📦 Your APK is ready.\n"
                    "Sending your application..."
                )

            disposition = resp.headers.get(
                "Content-Disposition", ""
            )

            match = re.search(
                r'filename=(?:"([^"]+)"|([^;]+))',
                disposition
            )

            apk_name = (
                (match.group(1) or match.group(2)).strip()
                if match else f"{app_name}.apk"
            )

            if not apk_name.lower().endswith(".apk"):
                apk_name += ".apk"

            bot.send_document(
                chat_id,
                (apk_name, resp.content),
                caption=(
                    "🎉 <b>BUILD COMPLETE!</b>\n\n"
                    f"📦 <code>{escape(apk_name)}</code>\n\n"
                    "✅ Your Android APK is ready.\n\n"
                    "⚠️ Test build only. This APK uses a "
                    "temporary signing key."
                ),
                reply_markup=kb_main_menu()
            )

        else:

            error_text = (
                f"❌ <b>BUILD FAILED</b> "
                f"({resp.status_code})\n\n"
                f"{api_error_message(resp)}"
            )

            if ui_message:
                edit_ui_message(
                    ui_message,
                    error_text
                )

            bot.send_message(
                chat_id,
                "👇 <b>Choose an option to continue.</b>",
                reply_markup=kb_main_menu()
            )

    except requests.RequestException as exc:

        logger.exception("Build API request failed")

        stop_event.set()

        if animation_thread:
            animation_thread.join(timeout=2)

        if ui_message:
            edit_ui_message(
                ui_message,
                "❌ <b>BUILD SERVICE UNAVAILABLE</b>\n\n"
                f"{escape(str(exc))}\n\n"
                "Please try again later."
            )

        bot.send_message(
            chat_id,
            "👇 <b>Choose an option to continue.</b>",
            reply_markup=kb_main_menu()
        )

    except Exception as exc:

        logger.exception("Unexpected build error")

        stop_event.set()

        if animation_thread:
            animation_thread.join(timeout=2)

        bot.send_message(
            chat_id,
            "❌ <b>Unexpected error</b>\n\n"
            f"{escape(str(exc))}",
            reply_markup=kb_main_menu()
        )

    finally:

        stop_event.set()
        reset(chat_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELP & ABOUT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HELP_TEXT = (
    "<b>📘 HOW IT WORKS</b>\n\n"
    "<b>1.</b> Choose 🚀 Create APK\n\n"
    "<b>2.</b> Enter your app name\n\n"
    "<b>3.</b> Upload HTML, HTM or ZIP\n\n"
    "<b>4.</b> Upload PNG or JPEG icon\n\n"
    "<b>5.</b> Review and build your APK\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ Builds are for testing only.\n"
    "Each APK uses a temporary signing key."
)

ABOUT_TEXT = (
    "<b>ℹ️ ABOUT APK CONVERTER STUDIO</b>\n\n"
    "🌐 Website to Android APK converter.\n\n"
    "<b>Supported files:</b>\n"
    "• HTML\n"
    "• HTM\n"
    "• ZIP\n"
    "• PNG\n"
    "• JPEG\n\n"
    "<b>Output:</b> Android 6.0+ test APK\n\n"
    "<i>Powered by CreatorX Build API.</i>"
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TELEGRAM COMMANDS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(commands=["start", "menu"])
def cmd_start(message):

    reset(message.chat.id)
    send_home(message.chat.id)


@bot.message_handler(commands=["help"])
def cmd_help(message):

    bot.send_message(
        message.chat.id,
        HELP_TEXT,
        reply_markup=kb_back()
    )


@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):

    cancel_flow(message.chat.id)


@bot.message_handler(commands=["newapk"])
def cmd_newapk(message):

    start_flow(message.chat.id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BOTTOM BUTTON HANDLERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(
    func=lambda m: m.text == "🚀 Create APK"
)
def btn_create(message):

    start_flow(message.chat.id)


@bot.message_handler(
    func=lambda m: m.text == "📘 How it works"
)
def btn_help(message):

    bot.send_message(
        message.chat.id,
        HELP_TEXT,
        reply_markup=kb_back()
    )


@bot.message_handler(
    func=lambda m: m.text == "ℹ️ About"
)
def btn_about(message):

    bot.send_message(
        message.chat.id,
        ABOUT_TEXT,
        reply_markup=kb_back()
    )


@bot.message_handler(
    func=lambda m: m.text == "⬅️ Back to menu"
)
def btn_back(message):

    reset(message.chat.id)
    send_home(message.chat.id, intro=False)


@bot.message_handler(
    func=lambda m: m.text == "✖️ Cancel"
)
def btn_cancel(message):

    cancel_flow(message.chat.id)


@bot.message_handler(
    func=lambda m: m.text == "✏️ Rename"
)
def btn_rename(message):

    chat_id = message.chat.id

    if chat_id not in sessions:
        bot.send_message(
            chat_id,
            "⚠️ Session expired. Start again.",
            reply_markup=kb_main_menu()
        )
        return

    text = (
        progress(0, "New app name") +
        "✏️ <b>RENAME YOUR APPLICATION</b>\n\n"
        "Send your new app name."
    )

    flow_next(
        chat_id,
        text,
        step_app_name,
        True
    )


@bot.message_handler(
    func=lambda m: m.text == "✅ Build APK"
)
def btn_build(message):

    chat_id = message.chat.id

    if (
        chat_id not in sessions
        or "icon" not in sessions[chat_id]
    ):
        bot.send_message(
            chat_id,
            "⚠️ Session expired. Please start again.",
            reply_markup=kb_main_menu()
        )
        return

    bot.send_message(
        chat_id,
        "⚙️ <b>BUILD STARTED</b>\n\n"
        "Preparing your APK...",
        reply_markup=kb_main_menu()
    )

    threading.Thread(
        target=build_apk,
        args=(chat_id,),
        daemon=True
    ).start()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# FALLBACK
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@bot.message_handler(
    func=lambda m: True,
    content_types=["text", "document", "photo"]
)
def fallback(message):

    if message.chat.id in sessions:
        return

    bot.send_message(
        message.chat.id,
        "👇 <b>Choose an option from the menu below.</b>",
        reply_markup=kb_main_menu()
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# START BOT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == "__main__":

    ensure_banner()

    logger.info(
        "APK Converter Studio starting..."
    )

    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30
    )