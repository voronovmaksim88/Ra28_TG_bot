from colorama import init, Fore
from pymodbus.client import ModbusTcpClient
from datetime import datetime
import struct
import time
import os
from dotenv import load_dotenv
from telegram import Bot
import asyncio
from loguru import logger

init()

# Загружаем переменные окружения из файла .env
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Настройка логирования
os.makedirs("logs", exist_ok=True)
logger.add("logs/bot.log", rotation="1 day", retention="7 days", level="INFO")

# Функция для отправки сообщения в Telegram
async def send_telegram_message(message: str):
    """Отправляет сообщение в Telegram"""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            logger.info(f"✅ Сообщение отправлено в Telegram: {message}")
        except Exception as telegram_error:
            logger.error(f"❌ Ошибка отправки сообщения в Telegram: {telegram_error}")
            print(Fore.RED + f"Ошибка отправки сообщения в Telegram: {telegram_error}")
    else:
        logger.warning("⚠️ Telegram бот не настроен (отсутствуют TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID)")

# Отправляем сообщение о запуске
asyncio.run(send_telegram_message("main.py запущен"))

print(Fore.GREEN + "начинаем опрос Z037...")

# Параметры подключения
MODBUS_HOST = "5.128.70.180"
MODBUS_PORT = 8502
UNIT_ID = 247
REGISTER_ADDRESS_Tpod_SO = 5 # адрес регистра Температура подачи системы отопления

# Создаем клиент Modbus TCP
client = ModbusTcpClient(host=MODBUS_HOST, port=MODBUS_PORT)

try:
    while True:
        try:
            # Переподключаемся перед каждым запросом
            if not client.connected:
                if client.connect():
                    print(Fore.GREEN + "Подключение установлено")
                else:
                    print(Fore.RED + "Не удалось подключиться")
                    time.sleep(10)
                    continue

            # Читаем 2 регистра
            result = client.read_holding_registers(address=REGISTER_ADDRESS_Tpod_SO, count=2, device_id=UNIT_ID)

            if result.isError():
                print(Fore.RED + f"Ошибка чтения регистра: {result}")
                client.close()  # Закрываем соединение при ошибке
            else:
                # Преобразуем регистры в float
                high_word = result.registers[0]
                low_word = result.registers[1]

                float_bytes = struct.pack('>HH', high_word, low_word)
                float_value = struct.unpack('>f', float_bytes)[0]

                # Получаем текущую дату и время
                current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                print(f"{current_time} - Температура подачи системы отопления: {float_value:.1f} °С")

                # Закрываем соединение после успешного чтения
                client.close()

        except Exception as modbus_error:
            print(Fore.RED + f"Ошибка при чтении: {modbus_error}")
            client.close()  # Закрываем соединение при ошибке

        # Ждем 10 секунд перед следующим запросом
        time.sleep(10)

except KeyboardInterrupt:
    print(Fore.YELLOW + "\nОстановка опроса...")
finally:
    client.close()
    print(Fore.GREEN + "Соединение закрыто")