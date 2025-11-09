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
            result = client.read_holding_registers(address=REGISTER_ADDRESS, count=2, device_id=UNIT_ID)

            if result.isError():
                print(Fore.RED + f"Ошибка чтения регистра: {result}")
                client.close()  # Закрываем соединение при ошибке
            else:
                # Преобразуем регистры в float
                high_word = result.registers[0]
                low_word = result.registers[1]

                float_bytes = struct.pack('>HH', high_word, low_word)
                float_value = struct.unpack('>f', float_bytes)[0]

                print(f"Тподачи СО {float_value:.1f} градусов")

                # Закрываем соединение после успешного чтения
                client.close()

        except Exception as e:
            print(Fore.RED + f"Ошибка при чтении: {e}")
            client.close()  # Закрываем соединение при ошибке

        # Ждем 10 секунд перед следующим запросом
        time.sleep(10)

except KeyboardInterrupt:
    print(Fore.YELLOW + "\nОстановка опроса...")
finally:
    client.close()
    print(Fore.GREEN + "Соединение закрыто")