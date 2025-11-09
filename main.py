from colorama import init, Fore
from pymodbus.client import ModbusTcpClient
import struct
import time

init()

print(Fore.GREEN + "начинаем опрос Z037...")

# Параметры подключения
MODBUS_HOST = "5.128.70.180"
MODBUS_PORT = 8502
UNIT_ID = 247
REGISTER_ADDRESS = 5

# Создаем клиент Modbus TCP
client = ModbusTcpClient(host=MODBUS_HOST, port=MODBUS_PORT)

try:
    # Подключаемся к устройству
    if client.connect():
        print(Fore.GREEN + "Подключение установлено")

        while True:
            try:
                # Читаем 2 регистра (float = 32 бита = 2 регистра по 16 бит)
                # В pymodbus 3.11.3 используем параметр device_id
                result = client.read_holding_registers(address=REGISTER_ADDRESS, count=2, device_id=UNIT_ID)

                if result.isError():
                    print(Fore.RED + f"Ошибка чтения регистра: {result}")
                else:
                    # Преобразуем регистры в float (big-endian, IEEE 754)
                    # Регистры приходят как [high_word, low_word]
                    high_word = result.registers[0]
                    low_word = result.registers[1]
                    
                    # Объединяем в 32-битное число и преобразуем в float.
                    # Используем big-endian порядок байтов
                    float_bytes = struct.pack('>HH', high_word, low_word)
                    float_value = struct.unpack('>f', float_bytes)[0]

                    # Выводим значение
                    print(f"Тподачи СО {float_value:.1f} градусов")

            except Exception as e:
                print(Fore.RED + f"Ошибка при чтении: {e}")

            # Ждем 10 секунд перед следующим запросом
            time.sleep(10)

except KeyboardInterrupt:
    print(Fore.YELLOW + "\nОстановка опроса...")
finally:
    client.close()
    print(Fore.GREEN + "Соединение закрыто")