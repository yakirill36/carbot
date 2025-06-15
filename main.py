import asyncio
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from supabase import create_client, Client
import httpx

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
HIMERA_API_KEY = os.getenv("HIMERA_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Не заданы обязательные переменные окружения: BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_states = {}
pending_shutdowns = {}  # Для хранения запросов на завершение диалога

# Клавиатуры
contact_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Подтвердить номер телефона", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True
)

allow_direct_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Да"), KeyboardButton(text="Нет")]],
    resize_keyboard=True,
    one_time_keyboard=True
)

main_menu = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔍 Поиск по номеру авто")],
        [KeyboardButton(text="🚠 Поддержка")]
    ],
    resize_keyboard=True
)

shutdown_request_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="✅ Подтвердить завершение")],
        [KeyboardButton(text="❌ Продолжить общение")]
    ],
    resize_keyboard=True
)

async def search_himera(car_number: str):
    url = f"https://api.himera.search/v2/lookup?car_number={car_number}"
    headers = {"Authorization": f"Bearer {HIMERA_API_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            else:
                logging.warning(f"Himera API error: {response.status_code} - {response.text}")
    except Exception as e:
        logging.error(f"Ошибка обращения к Himera API: {e}")
    return None

async def cleanup_pending_shutdowns():
    while True:
        now = datetime.now()
        to_delete = []
        
        for user_id, shutdown_time in pending_shutdowns.items():
            if now > shutdown_time:
                target_id = user_states.get(user_id, {}).get('target_id')
                if target_id:
                    try:
                        await bot.send_message(
                            user_id,
                            "❌ Подтверждение не получено, диалог продолжается",
                            reply_markup=ReplyKeyboardMarkup(
                                keyboard=[[KeyboardButton(text="Завершить диалог")]],
                                resize_keyboard=True
                            )
                        )
                        await bot.send_message(
                            target_id,
                            "❌ Собеседник не подтвердил завершение, диалог продолжается",
                            reply_markup=ReplyKeyboardMarkup(
                                keyboard=[[KeyboardButton(text="Завершить диалог")]],
                                resize_keyboard=True
                            )
                        )
                    except:
                        pass
                    # Возвращаем обычное состояние диалога
                    if user_id in user_states:
                        user_states[user_id]['step'] = 'dialog'
                    if target_id in user_states:
                        user_states[target_id]['step'] = 'dialog'
                to_delete.append(user_id)
        
        for user_id in to_delete:
            del pending_shutdowns[user_id]
        
        await asyncio.sleep(10)  # Проверка каждые 10 секунд

async def on_startup():
    asyncio.create_task(cleanup_pending_shutdowns())

@dp.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username

    response = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
    if response.data:
        await message.answer("Вы уже зарегистрированы ✅", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}
        return

    user_states[user_id] = {"step": "awaiting_phone", "username": username}
    await message.answer("Добро пожаловать! 🚘\nПожалуйста, подтвердите номер телефона:", reply_markup=contact_keyboard)

@dp.message(lambda message: message.contact is not None)
async def contact_handler(message: Message):
    user_id = message.from_user.id
    phone_number = message.contact.phone_number
    username = message.from_user.username

    response = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
    if response.data:
        await message.answer("Вы уже зарегистрированы ✅", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}
        return

    supabase.table("users").insert({
        "telegram_id": user_id,
        "username": username,
        "phone_number": phone_number,
        "verified": False,
        "allow_direct": False,
        "source": "bot"
    }).execute()

    user_states[user_id] = {
        "step": "awaiting_car_number",
        "phone_number": phone_number,
        "username": username
    }
    await message.answer("Номер подтверждён ✅\nВведите номер автомобиля:", reply_markup=ReplyKeyboardRemove())

@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {"step": "idle"})

    # Обработка кнопок меню
    if text == "🔍 Поиск по номеру авто":
        await message.answer("Введите номер автомобиля для поиска:", reply_markup=ReplyKeyboardRemove())
        user_states[user_id] = {"step": "search_car"}
        return

    if text == "🚠 Поддержка":
        await message.answer("Опишите вашу проблему, мы передадим её в поддержку 🚰", reply_markup=ReplyKeyboardRemove())
        user_states[user_id] = {"step": "support_message"}
        return

    # Обработка завершения диалога
    if text == "Завершить диалог":
        target_id = state.get("target_id")
        if target_id:
            pending_shutdowns[user_id] = datetime.now() + timedelta(minutes=5)
            user_states[user_id]['step'] = 'awaiting_shutdown_confirmation'
            user_states[target_id]['step'] = 'shutdown_requested'
            
            await bot.send_message(
                target_id,
                "⚠️ Собеседник хочет завершить диалог. Подтвердите:",
                reply_markup=shutdown_request_keyboard
            )
            await message.answer(
                "⏳ Ожидаем подтверждения завершения от собеседника...",
                reply_markup=ReplyKeyboardRemove()
            )
        return

    # Обработка подтверждения завершения
    if state["step"] == "shutdown_requested" and text == "✅ Подтвердить завершение":
        target_id = state.get("target_id")
        if target_id and user_id in pending_shutdowns:
            await bot.send_message(
                target_id,
                "❌ Диалог завершён по соглашению сторон.",
                reply_markup=main_menu
            )
            await message.answer(
                "❌ Диалог завершён по соглашению сторон.",
                reply_markup=main_menu
            )
            user_states[user_id] = {"step": "idle"}
            user_states[target_id] = {"step": "idle"}
            if target_id in pending_shutdowns:
                del pending_shutdowns[target_id]
        return

    # Обработка отказа от завершения
    if state["step"] == "shutdown_requested" and text == "❌ Продолжить общение":
        target_id = state.get("target_id")
        if target_id and user_id in pending_shutdowns:
            await bot.send_message(
                target_id,
                "➡️ Собеседник решил продолжить диалог",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="Завершить диалог")]],
                    resize_keyboard=True
                )
            )
            await message.answer(
                "➡️ Диалог продолжается",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="Завершить диалог")]],
                    resize_keyboard=True
                )
            )
            user_states[user_id]['step'] = 'dialog'
            user_states[target_id]['step'] = 'dialog'
            if target_id in pending_shutdowns:
                del pending_shutdowns[target_id]
        return

    # Логика поддержки
    if state["step"] == "support_message":
        await bot.send_message(ADMIN_ID, f"📬 Запрос в поддержку:\nОт: @{message.from_user.username}\nID: {user_id}\nСообщение: {text}")
        await message.answer("Спасибо, ваш запрос передан! Мы свяжемся с вами при необходимости.", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}

    # Логика регистрации
    elif state["step"] == "awaiting_car_number":
        car_number = text.upper().replace(" ", "")
        supabase.table("users").update({"car_number": car_number}).eq("telegram_id", user_id).execute()
        user_states[user_id] = {**state, "car_number": car_number, "step": "awaiting_allow_direct"}
        await message.answer("Разрешаете другим пользователям писать вам в ЛС?", reply_markup=allow_direct_keyboard)

    elif state["step"] == "awaiting_allow_direct":
        allow_direct = text.lower() in ["да", "yes"]
        if text.lower() not in ["да", "yes", "нет", "no"]:
            await message.answer("Пожалуйста, выберите 'Да' или 'Нет'.", reply_markup=allow_direct_keyboard)
            return
        supabase.table("users").update({"verified": True, "allow_direct": allow_direct}).eq("telegram_id", user_id).execute()
        await message.answer("Регистрация завершена ✅", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}

    # Логика поиска и диалога
    elif state["step"] == "search_car":
        car_number = text.upper().replace(" ", "")
        result = supabase.table("users").select("*").eq("car_number", car_number).execute()
        target_user = result.data[0] if result.data else None

        if not target_user:
            himera_data = await search_himera(car_number)
            if himera_data and "car_number" in himera_data:
                new_user = {
                    "car_number": himera_data.get("car_number"),
                    "username": himera_data.get("telegram"),
                    "phone_number": himera_data.get("phone"),
                    "verified": False,
                    "allow_direct": False,
                    "source": "himera",
                    "telegram_id": None
                }
                supabase.table("users").insert(new_user).execute()
                target_user = new_user

        if target_user:
            target_id = target_user.get("telegram_id")
            allow_direct = target_user.get("allow_direct", False)
            username = target_user.get("username")
            target_car_number = target_user.get("car_number", "неизвестен")

            current_user_data = supabase.table("users").select("car_number").eq("telegram_id", user_id).execute()
            sender_car_number = current_user_data.data[0].get("car_number") if current_user_data.data else "неизвестен"

            if target_id and allow_direct:
                await message.answer(f"Пользователь найден: @{username if username else 'неизвестен'}\nВы можете написать ему напрямую.", reply_markup=main_menu)
  elif target_id:
                user_states[user_id] = {
                    "step": "awaiting_first_message",
                    "target_id": target_id,
                    "sender_car_number": sender_car_number,
                    "target_car_number": target_car_number
                }
                user_states[target_id] = {
                    "step": "dialog",
                    "target_id": user_id,
                    "sender_car_number": sender_car_number,
                    "target_car_number": target_car_number
                }

                                await message.answer(
                    f"🔹 Начинаем диалог с владельцем авто {target_car_number}.\n"
                    "Напишите ваше первое сообщение:",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="Завершить диалог")]],
                        resize_keyboard=True
                    )
                )

          await bot.send_message(
                    target_id,
                    f"🔹 Владелец авто {sender_car_number} начал с вами диалог.\n"
                    "Ожидайте первое сообщение...",
                    reply_markup=ReplyKeyboardMarkup(
                        keyboard=[[KeyboardButton(text="Завершить диалог")]],
                        resize_keyboard=True
                    )
                )
                return
            else:
                await message.answer("Этот пользователь пока не зарегистрирован в боте.", reply_markup=main_menu)
        else:
            await message.answer("Пользователь не найден даже через Himera.", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}

    # Логика пересылки сообщений
    elif state["step"] == "awaiting_first_message":
        target_id = state.get("target_id")
        if not target_id:
            await message.answer("❌ Ошибка: получатель не найден.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
            return

         try:
            # Отправляем сообщение получателю
            await bot.send_message(
                target_id,
                f"📩 Сообщение от владельца авто {state.get('sender_car_number', 'неизвестен')}:\n\n{text}",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="Завершить диалог")]],
                    resize_keyboard=True
                )
            )
            # Подтверждение отправителю
            await message.answer(
                "✅ Сообщение доставлено!",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="Завершить диалог")]],
                    resize_keyboard=True
                )
            )
            # Меняем состояние на обычный диалог
            user_states[user_id]['step'] = 'dialog'
        except Exception as e:
            logging.error(f"Ошибка отправки: {e}")
            await message.answer(
                "❌ Не удалось отправить сообщение. Пользователь, возможно, заблокировал бота.",
                reply_markup=main_menu
            )
            user_states[user_id] = {"step": "idle"}

    # Дефолтная реакция
    else:
        await message.answer("Выберите действие из меню:", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}

async def main():
    logging.basicConfig(level=logging.INFO)
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
