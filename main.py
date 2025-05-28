from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from supabase import create_client, Client
import asyncio
import logging

# Конфигурация
BOT_TOKEN = ""
SUPABASE_URL = ""
SUPABASE_KEY = ""
ADMIN_ID = 534737101

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_states = {}

# Кнопки
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

@dp.message(lambda message: message.contact is not None)
async def contact_handler(message: Message):
    user_id = message.from_user.id
    phone_number = message.contact.phone_number
    username = message.from_user.username

    supabase.table("users").insert({
        "telegram_id": user_id,
        "username": username,
        "phone_number": phone_number,
        "verified": False,
        "allow_direct": False
    }).execute()

    user_states[user_id] = {"step": "awaiting_car_number", "phone_number": phone_number, "username": username}
    await message.answer("Номер подтверждён ✅\nВведите номер автомобиля:", reply_markup=ReplyKeyboardRemove())

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
        await message.answer("Разрешаете другим пользователям писать вам в ЛС? В ином случае сообщения будут приходить в бот.", reply_markup=allow_direct_keyboard)

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

        if result.data:
            target_user = result.data[0]
            target_id = target_user["telegram_id"]
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
                f"Пользователь найден: @{target_user.get('username')}\nВведите сообщение:",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="Завершить диалог")]],
                    resize_keyboard=True
                )
            )
            await bot.send_message(
                target_id,
                f"🚗 Пользователь с номером авто {car_number} начал с вами диалог.\nВы можете отвечать здесь.\nВведите сообщение:",
                reply_markup=ReplyKeyboardMarkup(
                    keyboard=[[KeyboardButton(text="Завершить диалог")]],
                    resize_keyboard=True
                )
            )
        else:
            await message.answer(
                "Пользователь не зарегистрирован в системе, отправьте приглашение в наш бот, если знаете его контакт.",
                reply_markup=main_menu
            )
            user_states[user_id] = {"step": "idle"}

    elif state["step"] == "dialog":
        if text.lower() == "завершить диалог":
            target_id = state["target_id"]
            await message.answer("Ожидаем подтверждение второго пользователя на завершение...")
            await bot.send_message(target_id, "Пользователь хочет завершить диалог. Подтвердите (Да/Нет).")
            user_states[target_id] = {"step": "confirm_end", "initiator_id": user_id}
            return

        msg = text
        target_id = state["target_id"]
        car_number = state.get("car_number", "неизвестен")

        try:
            await bot.send_message(
                target_id,
                f"📩 Входящее сообщение от {car_number}:\n{msg}"
            )
            await message.answer("Сообщение отправлено ✅")
        except:
            await message.answer("❌ Ошибка при отправке.")

    elif state["step"] == "confirm_end":
        if text.lower() in ["да", "yes"]:
            initiator_id = state["initiator_id"]
            await bot.send_message(initiator_id, "Пользователь подтвердил завершение диалога ✅", reply_markup=main_menu)
            await message.answer("Диалог завершён ✅", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
            user_states[initiator_id] = {"step": "idle"}
        else:
            await message.answer("Диалог продолжается.", reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="Завершить диалог")]],
                resize_keyboard=True
            ))
            initiator_id = state["initiator_id"]
            user_states[user_id] = {"step": "dialog", "target_id": initiator_id}
            user_states[initiator_id] = {"step": "dialog", "target_id": user_id}

# Запуск
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
