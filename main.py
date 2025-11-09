from colorama import init, Fore
from pymodbus.client import ModbusTcpClient
from datetime import datetime, time as dtime
import struct
import time
import os
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CallbackContext
from loguru import logger
import threading
from collections import deque

init()

# Загружаем переменные окружения из файла .env
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Настройка логирования
os.makedirs("logs", exist_ok=True)
logger.add("logs/bot.log", rotation="1 day", retention="7 days", level="INFO")

# Функция для отправки сообщения в Telegram (используется при запуске)
async def send_startup_notification(app):
    """Отправляет уведомление о запуске бота"""
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            await app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text="🚀 main.py запущен\n\n"
                     "✅ Modbus опрос активен (каждые 10 сек)\n"
                     "📊 Расчет средней температуры за час (360 измерений)\n"
                     "⏰ Ежедневный отчет: 14:20 UTC"
            )
            logger.info("✅ Уведомление о запуске отправлено в Telegram")
        except Exception as telegram_error:
            logger.error(f"❌ Ошибка отправки уведомления: {telegram_error}")
            print(Fore.RED + f"Ошибка отправки сообщения в Telegram: {telegram_error}")
    else:
        logger.warning("⚠️ Telegram бот не настроен (отсутствуют TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID)")

print(Fore.GREEN + "Инициализация... начинаем опрос Z037..." + Fore.RESET)

# Параметры подключения
MODBUS_HOST = "5.128.70.180"
MODBUS_PORT = 8502
UNIT_ID = 247
REGISTER_ADDRESS_Tpod_SO = 5 # адрес регистра Температура подачи системы отопления

# Глобальные переменные для хранения температуры
last_temperature = None
# Массив для хранения 360 последних значений температуры (360 * 10 сек = 1 час)
temperature_history = deque(maxlen=360)
temperature_lock = threading.Lock()

# Функция для опроса Modbus (работает в отдельном потоке)
def modbus_polling_loop():
    """Постоянно опрашивает Modbus и обновляет температуру"""
    global last_temperature, temperature_history
    
    client = ModbusTcpClient(host=MODBUS_HOST, port=MODBUS_PORT)
    
    try:
        while True:
            try:
                # Переподключаемся перед каждым запросом
                if not client.connected:
                    if client.connect():
                        print(Fore.GREEN + "Подключение установлено")
                        logger.info("✅ Modbus: подключение установлено")
                    else:
                        print(Fore.RED + "Не удалось подключиться")
                        logger.warning("⚠️ Modbus: не удалось подключиться")
                        time.sleep(10)
                        continue

                # Читаем 2 регистра
                result = client.read_holding_registers(address=REGISTER_ADDRESS_Tpod_SO, count=2, device_id=UNIT_ID)

                # Проверяем результат на ошибку
                if hasattr(result, 'isError') and result.isError():
                    print(Fore.RED + f"Ошибка чтения регистра: {result}")
                    logger.error(f"❌ Modbus: ошибка чтения регистра: {result}")
                    client.close()
                elif not hasattr(result, 'registers'):
                    print(Fore.RED + "Ошибка: некорректный ответ от контроллера")
                    logger.error("❌ Modbus: некорректный ответ, нет атрибута registers")
                    client.close()
                else:
                    # Преобразуем регистры в float
                    high_word = result.registers[0]
                    low_word = result.registers[1]

                    float_bytes = struct.pack('>HH', high_word, low_word)
                    temp_pod_so_float_value = struct.unpack('>f', float_bytes)[0]

                    # Сохраняем температуру в глобальную переменную (thread-safe)
                    with temperature_lock:
                        last_temperature = temp_pod_so_float_value
                        # Добавляем температуру в массив для расчета среднего за час
                        temperature_history.append(temp_pod_so_float_value)

                    # Получаем текущую дату и время
                    current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
                    
                    # Рассчитываем среднюю температуру за час
                    with temperature_lock:
                        if len(temperature_history) > 0:
                            avg_temp = sum(temperature_history) / len(temperature_history)
                            count = len(temperature_history)
                        else:
                            avg_temp = temp_pod_so_float_value
                            count = 1
                    
                    print(f"{current_time} - Температура подачи СО: {temp_pod_so_float_value:.1f} °С | "
                          f"Средняя за час: {avg_temp:.1f} °С (измерений: {count}/360)")
                    logger.debug(f"📊 Температура: {temp_pod_so_float_value:.1f} °С, средняя: {avg_temp:.1f} °С")

                    # Закрываем соединение после успешного чтения
                    client.close()

            except Exception as modbus_error:
                print(Fore.RED + f"Ошибка при чтении: {modbus_error}")
                logger.error(f"❌ Modbus: ошибка при чтении: {modbus_error}")
                client.close()

            # Ждем 10 секунд перед следующим запросом
            time.sleep(10)

    except KeyboardInterrupt:
        print(Fore.YELLOW + "\nОстановка опроса...")
        logger.info("🛑 Остановка Modbus опроса")
    finally:
        client.close()
        print(Fore.GREEN + "Соединение закрыто")
        logger.info("🔌 Modbus: соединение закрыто")

# Асинхронная функция для ежедневной отправки температуры
async def daily_temperature_report(context: CallbackContext) -> None:
    """Ежедневно в 14:20 UTC отправляет отчет о температуре"""
    logger.info("🕐 Запуск ежедневного отчета о температуре")
    
    if not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ TELEGRAM_CHAT_ID не настроен, пропускаем отправку отчета")
        return
    
    try:
        # Получаем последнюю температуру и рассчитываем среднюю (thread-safe)
        with temperature_lock:
            current_temp = last_temperature
            # Рассчитываем среднюю температуру за час
            if len(temperature_history) > 0:
                avg_temp = sum(temperature_history) / len(temperature_history)
                history_count = len(temperature_history)
            else:
                avg_temp = None
                history_count = 0
        
        # Формируем сообщение
        current_date = datetime.now().strftime("%d.%m.%Y")
        
        if current_temp is not None:
            # Формируем текст с текущей температурой
            message = (
                f"📊 Ежедневный отчет о температуре\n\n"
                f"📅 Дата: {current_date}\n"
                f"🕐 Время: 14:20 UTC\n\n"
                f"🌡️ Текущая температура подачи СО: {current_temp:.1f} °С\n"
            )
            
            # Добавляем среднюю температуру, если есть данные
            if avg_temp is not None and history_count > 0:
                message += f"📈 Средняя температура за час: {avg_temp:.1f} °С\n"
                message += f"📊 Количество измерений: {history_count}/360"
                avg_temp_str = f"{avg_temp:.1f}"
            else:
                message += f"⚠️ Недостаточно данных для расчета средней температуры"
                avg_temp_str = "N/A"
                
            logger.info(f"📤 Отправка отчета: текущая {current_temp:.1f} °С, средняя {avg_temp_str} °С")
        else:
            message = (
                f"📊 Ежедневный отчет о температуре\n\n"
                f"📅 Дата: {current_date}\n"
                f"🕐 Время: 14:20 UTC\n\n"
                f"⚠️ Данные о температуре недоступны\n"
                f"(возможно, нет связи с контроллером)"
            )
            logger.warning("⚠️ Отправка отчета: данные о температуре недоступны")
        
        # Отправляем сообщение
        await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.success("✅ Ежедневный отчет о температуре отправлен")
        
    except Exception as report_error:
        logger.error(f"❌ Ошибка при отправке ежедневного отчета: {report_error}")
        try:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=f"❌ Ошибка при формировании отчета:\n{str(report_error)}"
            )
        except Exception as send_error:
            logger.error(f"❌ Не удалось отправить сообщение об ошибке: {send_error}")

# Главная функция
def main():
    """Запускает Telegram бота и Modbus опрос"""
    # Создаём приложение Telegram-бота
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Отправляем уведомление о запуске (выполнится один раз через 2 секунды)
    app.job_queue.run_once(
        lambda context: send_startup_notification(app),
        when=2
    )
    
    # Планируем ежедневную отправку температуры в 14:20 UTC
    app.job_queue.run_daily(
        daily_temperature_report,
        time=dtime(hour=14, minute=20)
    )
    
    logger.success("🚀 Telegram бот настроен")
    logger.info("⏰ Расписание: отчет о температуре каждый день в 14:20 UTC")
    
    # Запускаем Modbus опрос в отдельном потоке
    modbus_thread = threading.Thread(target=modbus_polling_loop, daemon=True)
    modbus_thread.start()
    logger.info("🔄 Modbus опрос запущен в отдельном потоке")
    
    print(Fore.GREEN + "✅ Бот запущен. Ожидание команд и выполнение по расписанию..." + Fore.RESET)
    print(Fore.CYAN + "⏰ Ежедневный отчет о температуре: 14:20 UTC" + Fore.RESET)
    
    # Запускаем polling — бот начинает работу
    app.run_polling()

# Точка входа в программу
if __name__ == "__main__":
    main()