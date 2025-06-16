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

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
HIMERA_API_KEY = os.getenv("HIMERA_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0")) # Убедитесь, что ADMIN_ID установлен в .env

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY or not HIMERA_API_KEY:
    raise ValueError("❌ Не заданы обязательные переменные окружения: BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY, HIMERA_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Глобальные состояния пользователей
# user_states: {user_id: {"step": "...", "target_id": ..., "sender_car_number": ..., "target_car_number": ...}}
user_states = {}
# pending_shutdowns: {initiator_user_id: {"target_id": ..., "shutdown_time": datetime_object}}
pending_shutdowns = {}

# --- Клавиатуры ---
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

dialog_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Завершить диалог")]
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

# --- Вспомогательные функции ---
async def search_himera(car_number: str):
    url = f"https://api.himera.search/v2/lookup?car_number={car_number}"
    headers = {"Authorization": f"Bearer {HIMERA_API_KEY}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, timeout=10) # Добавил таймаут
            response.raise_for_status() # Вызовет исключение для ошибок 4xx/5xx
            return response.json()
    except httpx.RequestError as e:
        logging.error(f"Ошибка при запросе к Himera API для номера {car_number}: {e}")
    except httpx.HTTPStatusError as e:
        logging.warning(f"Himera API вернул ошибку {e.response.status_code} для номера {car_number}: {e.response.text}")
    except Exception as e:
        logging.error(f"Неизвестная ошибка обращения к Himera API для номера {car_number}: {e}")
    return None

async def cleanup_pending_shutdowns():
    while True:
        now = datetime.now()
        to_delete = []
        
        for initiator_id, data in pending_shutdowns.items():
            shutdown_time = data["shutdown_time"]
            target_id = data["target_id"]

            if now > shutdown_time:
                logging.info(f"Таймаут подтверждения завершения для пользователя {initiator_id} и {target_id}")
                
                # Возвращаем обычное состояние диалога для обоих
                if initiator_id in user_states:
                    user_states[initiator_id]['step'] = 'dialog'
                if target_id in user_states:
                    user_states[target_id]['step'] = 'dialog'
                
                try:
                    # Сообщение инициатору, что подтверждение не получено
                    await bot.send_message(
                        initiator_id,
                        "❌ Подтверждение завершения диалога не получено от собеседника, диалог продолжается.",
                        reply_markup=dialog_keyboard
                    )
                except Exception as e:
                    logging.warning(f"Не удалось отправить сообщение инициатору {initiator_id} о таймауте завершения: {e}")
                
                try:
                    # Сообщение цели, что инициатор отозвал запрос или произошел таймаут
                    await bot.send_message(
                        target_id,
                        "❌ Собеседник не подтвердил завершение или истекло время ожидания, диалог продолжается.",
                        reply_markup=dialog_keyboard
                    )
                except Exception as e:
                    logging.warning(f"Не удалось отправить сообщение цели {target_id} о таймауте завершения: {e}")
                
                to_delete.append(initiator_id)
        
        for initiator_id in to_delete:
            del pending_shutdowns[initiator_id]
        
        await asyncio.sleep(10)  # Проверка каждые 10 секунд

async def on_startup():
    logging.info("Бот запущен. Запускаем задачу очистки pending_shutdowns.")
    asyncio.create_task(cleanup_pending_shutdowns())

# --- Обработчики сообщений ---
@dp.message(CommandStart())
async def start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username

    response = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
    if response.data:
        await message.answer("Вы уже зарегистрированы ✅", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}
        logging.info(f"Пользователь {user_id} уже зарегистрирован, установлен 'idle' статус.")
        return

    user_states[user_id] = {"step": "awaiting_phone", "username": username}
    await message.answer("Добро пожаловать! 🚘\nПожалуйста, подтвердите номер телефона:", reply_markup=contact_keyboard)
    logging.info(f"Новый пользователь {user_id}. Ожидаем номер телефона.")

@dp.message(lambda message: message.contact is not None)
async def contact_handler(message: Message):
    user_id = message.from_user.id
    phone_number = message.contact.phone_number
    username = message.from_user.username if message.from_user.username else f"id_{user_id}"

    response = supabase.table("users").select("*").eq("telegram_id", user_id).execute()
    if response.data:
        await message.answer("Вы уже зарегистрированы ✅", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}
        logging.info(f"Пользователь {user_id} отправил контакт, но уже зарегистрирован.")
        return

    try:
        supabase.table("users").insert({
            "telegram_id": user_id,
            "username": username,
            "phone_number": phone_number,
            "verified": False,
            "allow_direct": False,
            "source": "bot"
        }).execute()
        logging.info(f"Пользователь {user_id} успешно зарегистрирован с номером {phone_number}.")
    except Exception as e:
        logging.error(f"Ошибка при сохранении пользователя {user_id} в Supabase: {e}")
        await message.answer("Произошла ошибка при регистрации. Пожалуйста, попробуйте еще раз.", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}
        return

    user_states[user_id] = {
        "step": "awaiting_car_number",
        "phone_number": phone_number,
        "username": username
    }
    await message.answer("Номер подтверждён ✅\nВведите номер автомобиля:", reply_markup=ReplyKeyboardRemove())
    logging.info(f"Пользователь {user_id} подтвердил номер, ожидаем номер авто.")

@dp.message()
async def handle_message(message: Message):
    user_id = message.from_user.id
    text = message.text.strip()
    state = user_states.get(user_id, {"step": "idle"})
    logging.info(f"User {user_id} current state: {state['step']}, message: {text[:50]}") # Логируем состояние и сообщение

    # --- Обработка кнопок меню ---
    if text == "🔍 Поиск по номеру авто":
        await message.answer("Введите номер автомобиля для поиска:", reply_markup=ReplyKeyboardRemove())
        user_states[user_id] = {"step": "search_car"}
        logging.info(f"User {user_id} перешел к поиску авто.")
        return

    if text == "🚠 Поддержка":
        await message.answer("Опишите вашу проблему, мы передадим её в поддержку 🚰", reply_markup=ReplyKeyboardRemove())
        user_states[user_id] = {"step": "support_message"}
        logging.info(f"User {user_id} перешел к поддержке.")
        return

    if text == "Завершить диалог":
        target_id = state.get("target_id")
        if target_id and user_states.get(target_id, {}).get("target_id") == user_id: # Проверяем, что есть активный диалог
            # Инициатор запроса на завершение
            pending_shutdowns[user_id] = {"target_id": target_id, "shutdown_time": datetime.now() + timedelta(minutes=3)} # 3 минуты на подтверждение
            user_states[user_id]['step'] = 'awaiting_shutdown_confirmation'
            user_states[target_id]['step'] = 'shutdown_requested' # Цель получает запрос

            try:
                await bot.send_message(
                    target_id,
                    "⚠️ Собеседник хочет завершить диалог. Подтвердите:",
                    reply_markup=shutdown_request_keyboard
                )
                await message.answer(
                    "⏳ Ожидаем подтверждения завершения от собеседника...",
                    reply_markup=ReplyKeyboardRemove()
                )
                logging.info(f"User {user_id} запросил завершение диалога с {target_id}.")
            except Exception as e:
                logging.error(f"Ошибка при отправке запроса на завершение {user_id} -> {target_id}: {e}")
                await message.answer("Произошла ошибка при запросе завершения диалога. Пожалуйста, попробуйте снова.", reply_markup=dialog_keyboard)
                user_states[user_id]['step'] = 'dialog' # Возвращаем в диалог
                user_states[target_id]['step'] = 'dialog'
                if user_id in pending_shutdowns:
                    del pending_shutdowns[user_id]
        else:
            await message.answer("Вы сейчас не в диалоге или диалог неактивен.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
        return

    # --- Обработка подтверждения/отказа завершения диалога ---
    # Пользователь, который получает запрос на завершение (target_id)
    if state["step"] == "shutdown_requested":
        # Находим инициатора, который запросил завершение с этим user_id
        initiator_id = None
        for initiator, data in pending_shutdowns.items():
            if data["target_id"] == user_id:
                initiator_id = initiator
                break

        if initiator_id:
            if text == "✅ Подтвердить завершение":
                logging.info(f"User {user_id} подтвердил завершение диалога с {initiator_id}.")
                try:
                    await bot.send_message(
                        initiator_id,
                        "❌ Диалог завершён по соглашению сторон.",
                        reply_markup=main_menu
                    )
                    await message.answer(
                        "❌ Диалог завершён по соглашению сторон.",
                        reply_markup=main_menu
                    )
                except Exception as e:
                    logging.warning(f"Не удалось отправить сообщение о завершении обоим {initiator_id}/{user_id}: {e}")
                
                user_states[user_id] = {"step": "idle"}
                user_states[initiator_id] = {"step": "idle"}
                if initiator_id in pending_shutdowns:
                    del pending_shutdowns[initiator_id]
                return

            elif text == "❌ Продолжить общение":
                logging.info(f"User {user_id} отказался завершать диалог с {initiator_id}.")
                try:
                    await bot.send_message(
                        initiator_id,
                        "➡️ Собеседник решил продолжить диалог.",
                        reply_markup=dialog_keyboard
                    )
                    await message.answer(
                        "➡️ Диалог продолжается.",
                        reply_markup=dialog_keyboard
                    )
                except Exception as e:
                    logging.warning(f"Не удалось отправить сообщение об отказе завершения обоим {initiator_id}/{user_id}: {e}")

                user_states[user_id]['step'] = 'dialog'
                user_states[initiator_id]['step'] = 'dialog'
                if initiator_id in pending_shutdowns:
                    del pending_shutdowns[initiator_id]
                return
            else:
                await message.answer("Пожалуйста, используйте кнопки для подтверждения или отказа.", reply_markup=shutdown_request_keyboard)
                return
        else:
            await message.answer("Нет активного запроса на завершение диалога от вас.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
            return
    
    # Пользователь, который запросил завершение и ждет подтверждения (initiator_id)
    if state["step"] == "awaiting_shutdown_confirmation":
        await message.answer("⏳ Вы уже запросили завершение диалога. Ожидаем подтверждения от собеседника.")
        return

    # --- Логика поддержки ---
    if state["step"] == "support_message":
        if ADMIN_ID != 0: # Проверяем, что ADMIN_ID установлен
            try:
                await bot.send_message(ADMIN_ID, f"📬 Запрос в поддержку:\nОт: @{message.from_user.username if message.from_user.username else user_id}\nID: {user_id}\nСообщение: {text}")
                await message.answer("Спасибо, ваш запрос передан! Мы свяжемся с вами при необходимости.", reply_markup=main_menu)
                user_states[user_id] = {"step": "idle"}
                logging.info(f"Запрос в поддержку от {user_id} отправлен админу.")
            except Exception as e:
                logging.error(f"Не удалось отправить запрос в поддержку админу {ADMIN_ID}: {e}")
                await message.answer("Не удалось отправить ваш запрос в поддержку. Произошла ошибка.", reply_markup=main_menu)
                user_states[user_id] = {"step": "idle"}
        else:
            await message.answer("Функция поддержки временно недоступна (не настроен ID администратора).", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
        return

    # --- Логика регистрации (продолжение) ---
    elif state["step"] == "awaiting_car_number":
        car_number = text.upper().replace(" ", "")
        try:
            supabase.table("users").update({"car_number": car_number}).eq("telegram_id", user_id).execute()
            user_states[user_id] = {**state, "car_number": car_number, "step": "awaiting_allow_direct"}
            await message.answer("Разрешаете другим пользователям писать вам в ЛС?", reply_markup=allow_direct_keyboard)
            logging.info(f"User {user_id} ввел номер авто {car_number}. Ожидаем разрешение ЛС.")
        except Exception as e:
            logging.error(f"Ошибка при обновлении car_number для пользователя {user_id}: {e}")
            await message.answer("Произошла ошибка при сохранении номера авто. Пожалуйста, попробуйте еще раз.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
        return

    elif state["step"] == "awaiting_allow_direct":
        allow_direct = text.lower() in ["да", "yes"]
        if text.lower() not in ["да", "yes", "нет", "no"]:
            await message.answer("Пожалуйста, выберите 'Да' или 'Нет'.", reply_markup=allow_direct_keyboard)
            return
        try:
            supabase.table("users").update({"verified": True, "allow_direct": allow_direct}).eq("telegram_id", user_id).execute()
            await message.answer("Регистрация завершена ✅", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
            logging.info(f"User {user_id} завершил регистрацию. allow_direct: {allow_direct}.")
        except Exception as e:
            logging.error(f"Ошибка при обновлении allow_direct для пользователя {user_id}: {e}")
            await message.answer("Произошла ошибка при завершении регистрации. Пожалуйста, попробуйте еще раз.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
        return

    # --- Логика поиска и начала диалога ---
    elif state["step"] == "search_car":
        car_number_to_search = text.upper().replace(" ", "")
        logging.info(f"User {user_id} ищет номер авто: {car_number_to_search}")

        # Сначала ищем в Supabase
        result = supabase.table("users").select("*").eq("car_number", car_number_to_search).execute()
        target_user = result.data[0] if result.data else None
        
        source = "supabase"
        if not target_user:
            logging.info(f"Авто {car_number_to_search} не найден в Supabase, пробуем Himera.")
            himera_data = await search_himera(car_number_to_search)
            if himera_data:
                # Если нашли в Himera, проверяем, есть ли такой car_number уже, чтобы не дублировать
                existing_user_by_himera_car = supabase.table("users").select("*").eq("car_number", himera_data.get("car_number")).execute().data
                if existing_user_by_himera_car:
                    target_user = existing_user_by_himera_car[0]
                    source = "supabase_from_himera_existing"
                    logging.info(f"Авто {car_number_to_search} найден через Himera, но уже есть в Supabase.")
                else:
                    new_user = {
                        "car_number": himera_data.get("car_number"),
                        "username": himera_data.get("telegram"), # Himera может возвращать username
                        "phone_number": himera_data.get("phone"),
                        "verified": False,
                        "allow_direct": False, # По умолчанию false для Himera-найденных
                        "source": "himera",
                        "telegram_id": None # ID телеграма неизвестен
                    }
                    try:
                        inserted_data = supabase.table("users").insert(new_user).execute()
                        target_user = inserted_data.data[0] # Получаем вставленного пользователя с его ID в Supabase
                        source = "himera_new"
                        logging.info(f"Авто {car_number_to_search} найден через Himera и добавлен в Supabase.")
                    except Exception as e:
                        logging.error(f"Ошибка при добавлении пользователя из Himera в Supabase: {e}")
                        target_user = None # Если ошибка, считаем, что не нашли
            else:
                logging.info(f"Авто {car_number_to_search} не найден ни в Supabase, ни в Himera.")

        if target_user:
            target_id = target_user.get("telegram_id")
            allow_direct = target_user.get("allow_direct", False)
            username = target_user.get("username")
            target_car_number = target_user.get("car_number", "неизвестен")

            current_user_data = supabase.table("users").select("car_number").eq("telegram_id", user_id).execute()
            sender_car_number = current_user_data.data[0].get("car_number") if current_user_data.data else "неизвестен"

            # Нельзя начать диалог с самим собой
            if target_id == user_id:
                await message.answer("Вы не можете начать диалог с самим собой.", reply_markup=main_menu)
                user_states[user_id] = {"step": "idle"}
                return

            # Если пользователь найден и разрешил прямые сообщения
            if target_id and allow_direct:
                if username:
                    await message.answer(f"Пользователь найден: @{username}\nВы можете написать ему напрямую.", reply_markup=main_menu)
                else:
                    await message.answer(f"Пользователь найден (ID: {target_id}). Он разрешил прямые сообщения. Вы можете попробовать найти его через ID или подождать, пока он сам напишет.", reply_markup=main_menu)
                user_states[user_id] = {"step": "idle"}
                logging.info(f"User {user_id} найден {target_id}, разрешены прямые сообщения. Диалог не требуется.")
            # Если пользователь найден, но не разрешил прямые сообщения, или его telegram_id неизвестен (найден через Himera)
            elif target_id: # Пользователь зарегистрирован в боте, но не разрешил прямые сообщения
                # Проверяем, не находится ли target_id уже в диалоге с кем-то
                target_state = user_states.get(target_id, {"step": "idle"})
                if target_state.get("step") in ["awaiting_first_message", "dialog", "awaiting_shutdown_confirmation", "shutdown_requested"]:
                    await message.answer(f"Владелец авто {target_car_number} сейчас уже занят в другом диалоге. Пожалуйста, попробуйте позже.", reply_markup=main_menu)
                    user_states[user_id] = {"step": "idle"}
                    logging.info(f"User {user_id} попытался начать диалог с {target_id}, но тот занят.")
                    return

                user_states[user_id] = {
                    "step": "awaiting_first_message", # Инициатор ждет подтверждения
                    "target_id": target_id,
                    "sender_car_number": sender_car_number,
                    "target_car_number": target_car_number
                }
                user_states[target_id] = {
                    "step": "dialog", # Цель сразу в состоянии диалога, как только примет первое сообщение
                    "target_id": user_id,
                    "sender_car_number": target_car_number, # Для цели отправитель - это car_number инициатора
                    "target_car_number": sender_car_number # Для цели цель - это ее собственный car_number
                }

                await message.answer(
                    f"🔹 Начинаем диалог с владельцем авто {target_car_number}.\n"
                    "Напишите ваше первое сообщение:",
                    reply_markup=dialog_keyboard
                )
                
                # Сообщаем целевому пользователю о входящем диалоге
                try:
                    await bot.send_message(
                        target_id,
                        f"🔹 Владелец авто {sender_car_number} хочет начать с вами диалог.\n"
                        "Ожидайте первое сообщение...",
                        reply_markup=dialog_keyboard
                    )
                    logging.info(f"User {user_id} начал диалог с {target_id}. Ожидается первое сообщение.")
                except Exception as e:
                    logging.error(f"Не удалось уведомить пользователя {target_id} о начале диалога от {user_id}: {e}")
                    await message.answer("Не удалось начать диалог с этим пользователем. Возможно, он заблокировал бота.", reply_markup=main_menu)
                    user_states[user_id] = {"step": "idle"}
                    del user_states[target_id] # Удаляем временное состояние
            else: # Пользователь не найден или найден через Himera, но без telegram_id
                await message.answer("Пользователь не найден в системе или его telegram_id неизвестен.", reply_markup=main_menu)
                user_states[user_id] = {"step": "idle"}
                logging.info(f"User {user_id} не смог найти пользователя {car_number_to_search} для диалога.")
        else:
            await message.answer("Пользователь не найден даже через Himera.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
            logging.info(f"User {user_id} не смог найти пользователя {car_number_to_search} вообще.")
        return

    # --- Логика пересылки сообщений в активном диалоге ---
    elif state["step"] == "awaiting_first_message" or state["step"] == "dialog":
        target_id = state.get("target_id")
        if not target_id:
            await message.answer("❌ Ошибка: получатель не найден для продолжения диалога.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
            logging.warning(f"User {user_id} в состоянии {state['step']}, но target_id отсутствует.")
            return

        # Проверяем, что целевой пользователь также находится в диалоге с текущим
        target_state = user_states.get(target_id)
        if not target_state or target_state.get("target_id") != user_id or target_state.get("step") not in ["awaiting_first_message", "dialog"]:
            await message.answer("❌ Собеседник вышел из диалога или его состояние некорректно. Диалог завершен.", reply_markup=main_menu)
            user_states[user_id] = {"step": "idle"}
            logging.warning(f"User {user_id} пытался отправить сообщение, но состояние {target_id} не позволяет. target_state: {target_state}")
            return

        try:
            # Отправляем сообщение получателю
            await bot.send_message(
                target_id,
                f"📩 Сообщение от владельца авто {state.get('sender_car_number', 'неизвестен')}:\n\n{text}",
                reply_markup=dialog_keyboard
            )
            # Подтверждение отправителю
            await message.answer(
                "✅ Сообщение доставлено!",
                reply_markup=dialog_keyboard
            )
            # Убеждаемся, что оба в состоянии 'dialog'
            user_states[user_id]['step'] = 'dialog'
            user_states[target_id]['step'] = 'dialog'
            logging.info(f"Сообщение от {user_id} к {target_id} доставлено. Оба в 'dialog' состоянии.")
        except Exception as e:
            logging.error(f"Ошибка отправки сообщения от {user_id} к {target_id}: {e}")
            await message.answer(
                "❌ Не удалось отправить сообщение. Возможно, пользователь заблокировал бота или произошла другая ошибка.",
                reply_markup=main_menu
            )
            # Завершаем диалог для обоих, если произошла ошибка отправки
            user_states[user_id] = {"step": "idle"}
            if target_id in user_states:
                user_states[target_id] = {"step": "idle"}
            try:
                await bot.send_message(target_id, "❌ Диалог завершен из-за ошибки отправки сообщения.")
            except:
                pass # Игнорируем ошибку, если не удалось отправить сообщение target_id
            logging.info(f"Диалог между {user_id} и {target_id} завершен из-за ошибки отправки.")
        return

    # --- Дефолтная реакция ---
    else:
        await message.answer("Выберите действие из меню:", reply_markup=main_menu)
        user_states[user_id] = {"step": "idle"}
        logging.info(f"User {user_id} в неизвестном состоянии '{state['step']}'. Сброс на 'idle'.")

async def main():
    logging.info("Starting bot polling...")
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
