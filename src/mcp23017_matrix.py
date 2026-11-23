"""
Драйвер матрицы 64 герконов на базе микросхемы расширителя портов MCP23017 по шине I2C.

Матрица 8x8:
- Port A (GPA0..GPA7): 8 строк (Row 0..7) — сконфигурированы как выходы (активный LOW).
- Port B (GPB0..GPB7): 8 столбцов (Col 0..7) — сконфигурированы как входы с подтяжкой к 3.3V (GPPU).
При замыкании геркона магнитом соответствующий столбец считывается как 0 (LOW).
"""

import time
from typing import Dict, List, Optional, Set

try:
    import smbus2
    SMBUS_AVAILABLE = True
except ImportError:
    try:
        import smbus as smbus2
        SMBUS_AVAILABLE = True
    except ImportError:
        smbus2 = None
        SMBUS_AVAILABLE = False

import config


# Регистры MCP23017 (при стандартном IOCON.BANK = 0)
REG_IODIRA = 0x00    # Направление Port A (0 = Out, 1 = In)
REG_IODIRB = 0x01    # Направление Port B (0 = Out, 1 = In)
REG_GPPUA  = 0x0C    # Подтягивающие резисторы 100 кОм Port A
REG_GPPUB  = 0x0D    # Подтягивающие резисторы 100 кОм Port B
REG_GPIOA  = 0x12    # Регистр чтения/записи Port A
REG_GPIOB  = 0x13    # Регистр чтения/записи Port B
REG_OLATA  = 0x14    # Выходной защелкивающийся регистр Port A
REG_OLATB  = 0x15    # Выходной защелкивающийся регистр Port B

CHESS_FILES = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
CHESS_RANKS = ['1', '2', '3', '4', '5', '6', '7', '8']


class MCP23017ReedMatrix:
    """Драйвер опроса матрицы герконов 8х8 через MCP23017."""

    def __init__(
        self,
        bus_num: int = 1,
        address: int = 0x20,
        mock: bool = False
    ):
        self.bus_num = bus_num
        self.address = address
        self.mock = mock
        self.bus = None
        self._mock_active_squares: Set[str] = set()

        if not self.mock:
            self._init_hardware()
        else:
            print("[MCP23017] Инициализирован в режиме эмуляции (--mock)")

    def _init_hardware(self):
        if not SMBUS_AVAILABLE:
            raise RuntimeError(
                "Библиотека smbus2 не установлена! Установите: pip install smbus2 "
                "или apt install python3-smbus"
            )

        try:
            self.bus = smbus2.SMBus(self.bus_num)
            # Настройка направлений портов:
            # Port A (строки) -> все 8 пинов как выходы (0x00)
            self.bus.write_byte_data(self.address, REG_IODIRA, 0x00)

            # Port B (столбцы) -> все 8 пинов как входы (0xFF)
            self.bus.write_byte_data(self.address, REG_IODIRB, 0xFF)

            # Включение внутренних подтягивающих резисторов на входах Port B
            self.bus.write_byte_data(self.address, REG_GPPUB, 0xFF)

            # Выставляем все строки Port A в высокий уровень (неактивны)
            self.bus.write_byte_data(self.address, REG_GPIOA, 0xFF)

            print(f"[MCP23017] Успешное подключение к шине I2C-{self.bus_num}, адрес 0x{self.address:02X}")
        except Exception as e:
            raise ConnectionError(
                f"Не удалось подключиться к MCP23017 на I2C-{self.bus_num} (0x{self.address:02X}): {e}\n"
                f"Проверьте проводку (SDA, SCL, 3.3V, GND) и команду: i2cdetect -y {self.bus_num}"
            )

    def scan(self) -> Dict[str, bool]:
        """
        Полное сканирование матрицы 8х8 (64 геркона).
        Возвращает словарь: {'a1': True/False, ..., 'h8': True/False}.
        True означает, что над герконом обнаружен магнит (замкнут).
        """
        if self.mock:
            # В режиме эмуляции возвращаем тестовые состояния
            result = {}
            for r in CHESS_RANKS:
                for f in CHESS_FILES:
                    sq = f"{f}{r}"
                    result[sq] = (sq in self._mock_active_squares)
            return result

        result = {}

        for row_idx in range(8):
            # Активируем одну строку (подаем 0 на соответствующий пин Port A)
            row_mask = (~(1 << row_idx)) & 0xFF
            self.bus.write_byte_data(self.address, REG_GPIOA, row_mask)

            # Микропауза для стабилизации фронтов сигнала в матрице
            time.sleep(0.0001)

            # Считываем состояние столбцов Port B
            col_val = self.bus.read_byte_data(self.address, REG_GPIOB)

            # 0 на входе означает, что геркон замкнут на землю через активную строку
            rank_str = CHESS_RANKS[row_idx]
            for col_idx in range(8):
                is_closed = ((col_val & (1 << col_idx)) == 0)
                file_str = CHESS_FILES[col_idx]
                square_name = f"{file_str}{rank_str}"
                result[square_name] = is_closed

        # Возвращаем все строки в пассивный высокий уровень
        self.bus.write_byte_data(self.address, REG_GPIOA, 0xFF)
        return result

    def get_active_squares(self) -> List[str]:
        """Возвращает список названий клеток, где в данный момент обнаружены магниты."""
        state = self.scan()
        return [sq for sq, is_active in state.items() if is_active]

    def set_mock_square(self, square_name: str, active: bool):
        """Управление состоянием клетки в режиме эмуляции."""
        square_name = square_name.lower().strip()
        if active:
            self._mock_active_squares.add(square_name)
        else:
            self._mock_active_squares.discard(square_name)

    def close(self):
        """Освобождение шины I2C."""
        if self.bus is not None:
            try:
                # Отключаем активность строк
                self.bus.write_byte_data(self.address, REG_GPIOA, 0xFF)
                self.bus.close()
            except Exception:
                pass
            self.bus = None
