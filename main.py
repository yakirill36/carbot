# main.py
import asyncio
import logging
import os
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

# Проверка переменных окружения
if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Не заданы обязательные переменные окружения: BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
user_states = {}

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

# Функция для запроса к API Himera
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

# Обработка команды /start
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
    await message.answer(
        "Добро пожаловать! 🚘\nПожалуйста, подтвердите номер телефона:",
        reply_markup=contact_keyboard
    )

# Обработка номера телефона
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

# Обработка всех остальных сообщений
@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id)

    if text == "🔍 Поиск по номеру авто":
        await message.answer("Введите номер автомобиля для поиска:", reply_markup=ReplyKeyboardRemove())
        user_states[user_id] = {"step": "search_car"}
        return

    if text == "🚠 Поддержка":
        await message.answer("Опишите вашу проблему, мы передадим её в поддержку 🚰", reply_markup=ReplyKeyboardRemove())
        user_states[user_id] = {"step": "support_message"}
        return

    if state is None:
        response = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
        if response.data:
            await message.answer("Вы уже зарегистрированы ✅", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
        else:
            await message.answer("Напишите /start, чтобы начать регистрацию")
        return

    if state["step"] == "support_message":
        await bot.send_message(ADMIN_ID, f"📬 Запрос в поддержку:\nОт: @{message.from_user.username}\nID: {user_id}\nСообщение: {text}")
        await message.answer("Спасибо, ваш запрос передан! Мы свяжемся с вами при необходимости.", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}

    elif state["step"] == "awaiting_car_number":
        car_number = text.upper().replace(" ", "")
        supabase.table("users").update({"car_number": car_number}).eq("telegram_id", user_id).execute()
        user_states[user_id] = {**state, "car_number": car_number, "step": "awaiting_allow_direct"}
        await message.answer("Разрешаете другим пользователям писать вам в ЛС?", reply_markup=allow_direct_keyboard)

    elif state["step"] == "awaiting_allow_direct":
        if text.lower() in ["да", "yes"]:
            allow_direct = True
        elif text.lower() in ["нет", "no"]:
            allow_direct = False
        else:
            await message.answer("Пожалуйста, выберите 'Да' или 'Нет'.", reply_markup=allow_direct_keyboard)
            return

        supabase.table("users").update({
            "verified": True,
            "allow_direct": allow_direct
        }).eq("telegram_id", user_id).execute()

        await message.answer("Регистрация завершена ✅", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}

    elif state["step"] == "search_car":
        car_number = text.upper().replace(" ", "")
        result = supabase.table("users").select("*").eq("car_number", car_number).execute()

        target_user = None
        if result.data:
            target_user = result.data[0]
        else:
            himera_data = await search_himera(car_number)
            if himera_data and "car_number" in himera_data:
                new_user = {
                    "car_number": himera_data.get("car_number"),
                    "username": himera_data.get("telegram", None),
                    "phone_number": himera_data.get("phone", None),
                    "verified": False,
                    "allow_direct": False,
                    "source": "himera",
                    "telegram_id": None
                }
                supabase.table("users").insert(new_user).execute()
                target_user = new_user

        if target_user:
            allow_direct = target_user.get("allow_direct", False)
            username = target_user.get("username")

            if allow_direct and username:
                await message.answer(
                    f"Пользователь найден: @{username}\nВы можете написать ему напрямую в Telegram.",
                    reply_markup=main_menu
                )
            else:
                target_id = target_user.get("telegram_id")
                if not target_id:
                    await message.answer("Этот пользователь пока не зарегистрирован в боте.", reply_markup=main_menu)
                else:
                    user_states[user_id] = {
                        "step": "dialog",
                        "target_id": target_id,
                        "car_number": car_number
                    }
                    user_states[target_id] = {
                        "step": "dialog",
                        "target_id": user_id,
                        "car_number": car_number
                    }

                    await message.answer(
                        "Пользователь найден, он не принимает ЛС. Можете общаться через бот.\nВведите сообщение:",
                        reply_markup=ReplyKeyboardMarkup(
                            keyboard=[[KeyboardButton(text="Завершить диалог")]],
                            resize_keyboard=True
                        )
                    )
                    await bot.send_message(
                        target_id,
                        f"🚗 Пользователь с номером авто {car_number} начал с вами диалог.\nВведите сообщение:",
                        reply_markup=ReplyKeyboardMarkup(
                            keyboard=[[KeyboardButton(text="Завершить диалог")]],
                            resize_keyboard=True
                        )
                    )
        else:
            await message.answer("Пользователь не найден даже через Himera.", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}

# Точка входа
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
