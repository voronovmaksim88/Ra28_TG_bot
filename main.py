from colorama import init, Fore
from pymodbus.client import ModbusTcpClient
from datetime import datetime, time as dtime, timezone
import struct
import time
import os
from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder, CallbackContext, CommandHandler
from loguru import logger
import threading
from collections import deque
import asyncio

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
                text=                     "🚀 main.py запущен\n\n"
                     "✅ Modbus опрос активен (каждые 10 сек)\n"
                     "📊 Отслеживаемые параметры:\n"
                     "   • Температура подачи СО (регистр 5)\n"
                     "   • Температура воздуха в котельной (регистр 7)\n"
                     "📊 Расчет средней температуры за час (360 измерений)\n"
                     "⚠️ Мониторинг аварии низкой температуры подачи СО (сетевая переменная: А: низкая Тпод СО)\n"
                     "⏰ Ежедневный отчет: 01:00 UTC\n\n"
                     "💡 Доступные команды:\n"
                     "   /temperature - показать текущую температуру"
            )
            logger.info("✅ Уведомление о запуске отправлено в Telegram")
        except Exception as telegram_error:
            logger.error(f"❌ Ошибка отправки уведомления: {telegram_error}")
            print(Fore.RED + f"Ошибка отправки сообщения в Telegram: {telegram_error}")
    else:
        logger.warning("⚠️ Telegram бот не настроен (отсутствуют TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID)")

# Функция для отправки предупреждения при переходе аварии низкой температуры в активное состояние
async def check_and_send_low_temp_alarm(bot_app, alarm_state):
    """Отправляет предупреждение при переходе аварии низкой температуры из 0 в 1."""
    global last_low_temp_alarm_state
    
    if not TELEGRAM_CHAT_ID:
        last_low_temp_alarm_state = alarm_state
        return
    
    is_rising_edge = last_low_temp_alarm_state is False and alarm_state is True
    last_low_temp_alarm_state = alarm_state

    if not is_rising_edge:
        return

    try:
        current_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        message = (
            "⚠️ ПРЕДУПРЕЖДЕНИЕ О НИЗКОЙ ТЕМПЕРАТУРЕ ⚠️\n\n"
            f"🕐 Время: {current_time}\n"
            "🌡️ Авария: низкая температура подачи в системе отопления\n"
            "📡 Источник: сетевая переменная \"А: низкая Тпод СО\" перешла из 0 в 1"
        )

        await bot_app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
        logger.warning("⚠️ Отправлено предупреждение: авария низкой температуры подачи СО")
        print(Fore.YELLOW + "⚠️ Отправлено предупреждение: авария низкой температуры подачи СО" + Fore.RESET)
    except Exception as error:
        logger.error(f"❌ Ошибка отправки предупреждения о низкой температуре: {error}")

print(Fore.GREEN + "Инициализация... начинаем опрос Z037..." + Fore.RESET)

# Параметры подключения
MODBUS_HOST = "5.128.70.180"
MODBUS_PORT = 8502
UNIT_ID = 247
REGISTER_ADDRESS_Tpod_SO = 5 # адрес регистра Температура подачи системы отопления
REGISTER_ADDRESS_Tvozd_kotel = 7 # адрес регистра Температура воздуха в котельной
REGISTER_ADDRESS_LOW_TEMP_ALARM = 0 # адрес сетевой переменной "А: низкая Тпод СО"

# Константа для хранения истории температур (360 * 10 сек = 1 час)
TEMP_HISTORY_SIZE = 360

# Глобальные переменные для хранения температуры подачи СО
last_temperature = None
# Массив для хранения последних значений температуры (360 * 10 сек = 1 час)
temperature_history = deque(maxlen=TEMP_HISTORY_SIZE)
temperature_lock = threading.Lock()
# Предыдущее состояние аварии низкой температуры подачи СО
last_low_temp_alarm_state = None

# Глобальные переменные для хранения температуры воздуха в котельной
last_temperature_air = None
# Массив для хранения последних значений температуры воздуха (360 * 10 сек = 1 час)
temperature_air_history = deque(maxlen=TEMP_HISTORY_SIZE)
temperature_air_lock = threading.Lock()

# Функция для опроса Modbus (работает в отдельном потоке)
def modbus_polling_loop(bot_app=None):
    """Постоянно опрашивает Modbus и обновляет температуру"""
    global last_temperature, temperature_history, last_temperature_air, temperature_air_history
    
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

                # Читаем сетевую переменную аварии низкой температуры подачи СО
                result_low_temp_alarm = client.read_holding_registers(
                    address=REGISTER_ADDRESS_LOW_TEMP_ALARM,
                    count=1,
                    device_id=UNIT_ID
                )

                if hasattr(result_low_temp_alarm, 'isError') and result_low_temp_alarm.isError():
                    print(Fore.RED + f"Ошибка чтения аварии низкой температуры подачи СО: {result_low_temp_alarm}")
                    logger.error(f"❌ Modbus: ошибка чтения аварии низкой температуры подачи СО: {result_low_temp_alarm}")
                elif not hasattr(result_low_temp_alarm, 'registers'):
                    print(Fore.RED + "Ошибка: некорректный ответ от контроллера (авария низкой температуры подачи СО)")
                    logger.error("❌ Modbus: некорректный ответ, нет атрибута registers (авария низкой температуры подачи СО)")
                else:
                    low_temp_alarm_state = bool(result_low_temp_alarm.registers[0])
                    logger.debug(f"⚠️ Авария низкой температуры подачи СО: {int(low_temp_alarm_state)}")

                    if bot_app:
                        try:
                            asyncio.run(check_and_send_low_temp_alarm(bot_app, low_temp_alarm_state))
                        except Exception as check_error:
                            logger.error(f"❌ Ошибка при проверке аварии низкой температуры: {check_error}")

                # Читаем 2 регистра для температуры подачи СО
                result = client.read_holding_registers(address=REGISTER_ADDRESS_Tpod_SO, count=2, device_id=UNIT_ID)

                # Проверяем результат на ошибку
                if hasattr(result, 'isError') and result.isError():
                    print(Fore.RED + f"Ошибка чтения регистра температуры подачи СО: {result}")
                    logger.error(f"❌ Modbus: ошибка чтения регистра температуры подачи СО: {result}")
                    client.close()
                elif not hasattr(result, 'registers'):
                    print(Fore.RED + "Ошибка: некорректный ответ от контроллера (температура подачи СО)")
                    logger.error("❌ Modbus: некорректный ответ, нет атрибута registers (температура подачи СО)")
                    client.close()
                else:
                    # Преобразуем регистры в float для температуры подачи СО
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
                    
                    # Читаем 2 регистра для температуры воздуха в котельной
                    result_air = client.read_holding_registers(address=REGISTER_ADDRESS_Tvozd_kotel, count=2, device_id=UNIT_ID)
                    
                    # Проверяем результат на ошибку
                    if hasattr(result_air, 'isError') and result_air.isError():
                        print(Fore.RED + f"Ошибка чтения регистра температуры воздуха: {result_air}")
                        logger.error(f"❌ Modbus: ошибка чтения регистра температуры воздуха: {result_air}")
                        temp_air_float_value = None
                        avg_temp_air = None
                        count_air = 0
                    elif not hasattr(result_air, 'registers'):
                        print(Fore.RED + "Ошибка: некорректный ответ от контроллера (температура воздуха)")
                        logger.error("❌ Modbus: некорректный ответ, нет атрибута registers (температура воздуха)")
                        temp_air_float_value = None
                        avg_temp_air = None
                        count_air = 0
                    else:
                        # Преобразуем регистры в float для температуры воздуха
                        high_word_air = result_air.registers[0]
                        low_word_air = result_air.registers[1]

                        float_bytes_air = struct.pack('>HH', high_word_air, low_word_air)
                        temp_air_float_value = struct.unpack('>f', float_bytes_air)[0]

                        # Сохраняем температуру воздуха в глобальную переменную (thread-safe)
                        with temperature_air_lock:
                            last_temperature_air = temp_air_float_value
                            # Добавляем температуру в массив для расчета среднего за час
                            temperature_air_history.append(temp_air_float_value)
                            
                            # Рассчитываем среднюю температуру воздуха за час
                            if len(temperature_air_history) > 0:
                                avg_temp_air = sum(temperature_air_history) / len(temperature_air_history)
                                count_air = len(temperature_air_history)
                            else:
                                avg_temp_air = temp_air_float_value
                                count_air = 1
                    
                    # Выводим обе температуры
                    if temp_air_float_value is not None:
                        print(f"{current_time} - Температура подачи СО: {temp_pod_so_float_value:.1f} °С | "
                              f"Средняя за час: {avg_temp:.1f} °С (измерений: {count}/{TEMP_HISTORY_SIZE}) | "
                              f"Температура воздуха: {temp_air_float_value:.1f} °С | "
                              f"Средняя за час: {avg_temp_air:.1f} °С (измерений: {count_air}/{TEMP_HISTORY_SIZE})")
                        logger.debug(f"📊 Температура подачи СО: {temp_pod_so_float_value:.1f} °С, средняя: {avg_temp:.1f} °С | "
                                   f"Температура воздуха: {temp_air_float_value:.1f} °С, средняя: {avg_temp_air:.1f} °С")
                    else:
                        print(f"{current_time} - Температура подачи СО: {temp_pod_so_float_value:.1f} °С | "
                              f"Средняя за час: {avg_temp:.1f} °С (измерений: {count}/{TEMP_HISTORY_SIZE}) | "
                              f"Температура воздуха: недоступна")
                        logger.debug(f"📊 Температура подачи СО: {temp_pod_so_float_value:.1f} °С, средняя: {avg_temp:.1f} °С | "
                                   f"Температура воздуха: недоступна")
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

# Вспомогательная функция для формирования отчёта о температуре
def generate_temperature_report(report_title="📊 Отчет о температуре"):
    """Формирует текст отчёта о температуре"""
    # Получаем последнюю температуру подачи СО и рассчитываем среднюю (thread-safe)
    with temperature_lock:
        current_temp = last_temperature
        # Рассчитываем среднюю температуру за час
        if len(temperature_history) > 0:
            avg_temp = sum(temperature_history) / len(temperature_history)
            history_count = len(temperature_history)
        else:
            avg_temp = None
            history_count = 0
    
    # Получаем последнюю температуру воздуха в котельной и рассчитываем среднюю (thread-safe)
    with temperature_air_lock:
        current_temp_air = last_temperature_air
        # Рассчитываем среднюю температуру воздуха за час
        if len(temperature_air_history) > 0:
            avg_temp_air = sum(temperature_air_history) / len(temperature_air_history)
            history_count_air = len(temperature_air_history)
        else:
            avg_temp_air = None
            history_count_air = 0
    
    # Формируем сообщение
    current_date = datetime.now().strftime("%d.%m.%Y")
    current_time_utc = datetime.now(timezone.utc).strftime("%H:%M")
    
    message = (
        f"{report_title}\n\n"
        f"📅 Дата: {current_date}\n"
        f"🕐 Время: {current_time_utc} UTC\n\n"
    )
    
    # Добавляем информацию о температуре подачи СО
    if current_temp is not None:
        message += f"🌡️ Текущая температура подачи СО: {current_temp:.1f} °С\n"
        
        # Добавляем среднюю температуру, если есть данные
        if avg_temp is not None and history_count > 0:
            message += f"📈 Средняя температура за час: {avg_temp:.1f} °С\n"
            message += f"📊 Количество измерений: {history_count}/{TEMP_HISTORY_SIZE}\n"
        else:
            message += f"⚠️ Недостаточно данных для расчета средней температуры\n"
    else:
        message += (
            f"⚠️ Данные о температуре подачи СО недоступны\n"
            f"(возможно, нет связи с контроллером)\n"
        )
    
    # Добавляем информацию о температуре воздуха в котельной
    message += "\n"
    if current_temp_air is not None:
        message += f"🌡️ Текущая температура воздуха в котельной: {current_temp_air:.1f} °С\n"
        
        # Добавляем среднюю температуру воздуха, если есть данные
        if avg_temp_air is not None and history_count_air > 0:
            message += f"📈 Средняя температура за час: {avg_temp_air:.1f} °С\n"
            message += f"📊 Количество измерений: {history_count_air}/{TEMP_HISTORY_SIZE}"
        else:
            message += f"⚠️ Недостаточно данных для расчета средней температуры воздуха"
    else:
        message += (
            f"⚠️ Данные о температуре воздуха недоступны\n"
            f"(возможно, нет связи с контроллером)"
        )
    
    return message

# Асинхронная функция для ежедневной отправки температуры
async def daily_temperature_report(context: CallbackContext) -> None:
    """Ежедневно отправляет отчет о температуре"""
    logger.info("🕐 Запуск ежедневного отчета о температуре")
    
    if not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ TELEGRAM_CHAT_ID не настроен, пропускаем отправку отчета")
        return
    
    try:
        message = generate_temperature_report("📊 Ежедневный отчет о температуре")
        
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

# Обработчик команды /temperature для показа температуры по запросу
async def temperature_command(update, _context: CallbackContext) -> None:
    """Обрабатывает команду /temperature и отправляет текущую температуру"""
    logger.info(f"📱 Получена команда /temperature от пользователя {update.effective_user.id}")
    
    try:
        message = generate_temperature_report("🌡️ Текущая температура отопления")
        
        # Отправляем сообщение
        await update.message.reply_text(message)
        logger.info("✅ Отчёт по команде /temperature отправлен")
        
    except Exception as cmd_error:
        logger.error(f"❌ Ошибка при обработке команды /temperature: {cmd_error}")
        try:
            await update.message.reply_text(
                f"❌ Ошибка при получении данных о температуре:\n{str(cmd_error)}"
            )
        except Exception as send_error:
            logger.error(f"❌ Не удалось отправить сообщение об ошибке: {send_error}")

# Главная функция
def main():
    """Запускает Telegram бота и Modbus опрос"""
    # Создаём приложение Telegram-бота
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Регистрируем обработчик команды /temperature
    app.add_handler(CommandHandler("temperature", temperature_command))
    logger.info("📝 Зарегистрирована команда /temperature")
    
    # Отправляем уведомление о запуске (выполнится один раз через 2 секунды)
    app.job_queue.run_once(
        lambda context: send_startup_notification(app),
        when=2
    )
    
    # Планируем ежедневную отправку температуры в 01:00 UTC
    app.job_queue.run_daily(
        daily_temperature_report,
        time=dtime(hour=1, minute=0)
    )

    logger.success("🚀 Telegram бот настроен")
    logger.info("⏰ Расписание: отчет о температуре каждый день в 01:00 UTC")
    
    # Запускаем Modbus опрос в отдельном потоке с передачей объекта бота
    modbus_thread = threading.Thread(target=modbus_polling_loop, args=(app,), daemon=True)
    modbus_thread.start()
    logger.info("🔄 Modbus опрос запущен в отдельном потоке")
    
    print(Fore.GREEN + "✅ Бот запущен. Ожидание команд и выполнение по расписанию..." + Fore.RESET)
    print(Fore.CYAN + "⏰ Ежедневный отчет о температуре: 01:00 UTC" + Fore.RESET)
    
    # Запускаем polling — бот начинает работу
    app.run_polling()

# Точка входа в программу
if __name__ == "__main__":
    main()
