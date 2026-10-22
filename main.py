import os
import re
import asyncio
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# 1. قراءة التوكين والمنفذ تلقائياً من إعدادات Render
TOKEN = os.getenv("BOT_TOKEN", "8952981547:AAE0eyZWfsBMtF45uy1I1AVzKld18HFDwEA")
PORT = int(os.getenv("PORT", 8080))

# ذاكرة مؤقتة لتخزين أوقات البيعات الشغالة
active_sales = {}

# دالة تحويل الدقائق إلى صيغة (ساعات ودقائق)
def format_time_left(minutes):
    if minutes <= 0:
        return "🎉 البيعة اكتملت الآن!"
    
    hours = minutes // 60
    rem_min = minutes % 60
    
    if hours > 0 and rem_min > 0:
        return f"باقي {hours} ساعة و {rem_min} دقيقة ⏳"
    elif hours > 0:
        return f"باقي {hours} ساعة ⏳"
    else:
        return f"باقي {rem_min} دقيقة ⏳"

# 2. سيرفر الـ Health Check لمنع Render و UptimeRobot من إيقاف البوت
async def handle_health_check(request):
    return web.Response(text="Cripps Bot is Active!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    app.router.add_get('/health', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

# 3. الاستجابة لأمر البيعة (مثل: بيعه 98 أو بيعة 50)
async def handle_sale_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text
    # التعرف على كلمة (بيعه/بيعة) واستخراج الرقم
    match = re.search(r'(?:بيعه|بيعة)\s*(\d+)', text, re.IGNORECASE)

    if match:
        goods_count = int(match.group(1))

        if goods_count < 0 or goods_count > 100:
            await update.message.reply_text("الرجاء كتابة رقم بضاعة بين 0 و 100!")
            return

        remaining_goods = 100 - goods_count
        remaining_minutes = remaining_goods * 2  # كل حبة = دقيقتين إنتاج

        if remaining_minutes == 0:
            await update.message.reply_text("🎉 البيعة مكتملة 100/100 بالفعل!")
            return

        # النص المستنسخ من فكرة الصورة
        caption = (
            "❗️ جيب للتاجر Supply كل 50 دقيقة.\n"
            "❗️ لا تطفي اللعبة.\n"
            "❗️ لا تدخل أي بوس ( كعضو )."
        )

        # الزر التفاعلي الأول مع تنسيق الساعات والدقائق
        button_text = format_time_left(remaining_minutes)
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(button_text, callback_data="refresh_time")]
        ])

        sent_msg = await update.message.reply_text(
            caption,
            reply_to_message_id=update.message.message_id,
            reply_markup=keyboard
        )

        # تسجيل وقت بداية البيعة في الذاكرة
        sale_key = f"{sent_msg.chat_id}_{sent_msg.message_id}"
        active_sales[sale_key] = {
            "start_time": datetime.utcnow(),
            "total_minutes": remaining_minutes,
            "user_name": update.effective_user.first_name
        }

        # جدولة تنبيه الانتهاء النهائي بعد مرور دقائق البيعة
        context.job_queue.run_once(
            sale_finished,
            remaining_minutes * 60,
            chat_id=sent_msg.chat_id,
            reply_to_message_id=update.message.message_id,
            data={"user_name": update.effective_user.first_name}
        )

# 4. التنبيه عند الانتهاء من البيعة
async def sale_finished(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    user_name = job.data.get("user_name", "يا بطل")
    await context.bot.send_message(
        chat_id=job.chat_id,
        text=f"🎉 **البيعة جاهزة 100/100!**\nيا {user_name} نادِ الفزعة وجهّز العربة للبيع الآن! 🤠📦",
        reply_to_message_id=job.reply_to_message_id,
        parse_mode="Markdown"
    )

# 5. تحديث الوقت المتبقي فور ضغط الزر
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not query.message:
        return

    sale_key = f"{query.message.chat_id}_{query.message.message_id}"
    sale_data = active_sales.get(sale_key)

    if not sale_data:
        await query.answer("تم انتهاء العداد أو إعادة تشغيل البوت.", show_alert=True)
        return

    # حساب الفرق بين الوقت الحالي ووقت البدء
    elapsed_seconds = (datetime.utcnow() - sale_data["start_time"]).total_seconds()
    elapsed_minutes = int(elapsed_seconds // 60)
    minutes_left = sale_data["total_minutes"] - elapsed_minutes

    # تحويل المتبقي إلى ساعات ودقائق
    new_text = format_time_left(minutes_left)

    # تحديث نص الزر بنفس المكان
    new_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(new_text, callback_data="refresh_time")]
    ])

    try:
        await query.edit_message_reply_markup(reply_markup=new_keyboard)
    except Exception:
        pass

# 6. التشغيل والربط
async def main():
    await start_web_server()

    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_sale_message))
    application.add_handler(CallbackQueryHandler(handle_button_click))

    async with application:
        await application.start()
        await application.updater.start_polling()
        await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
  
