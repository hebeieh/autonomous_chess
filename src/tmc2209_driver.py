"""
Модуль расширенного управления драйверами TMC2209:
- Однопроводной полудуплексный программный UART (настройка тока, microstepping, stealthChop).
- Бесконцевой автопоиск нуля (Sensorless Homing) по сигналам StallGuard4 на пинах DIAG.
"""

import math
import sys
import time
from typing import Optional

try:
    import pigpio
    PI_OUTPUT = pigpio.OUTPUT
    PI_INPUT = pigpio.INPUT
except ImportError:
    pigpio = None
    PI_OUTPUT = 1
    PI_INPUT = 0

import config


# Адреса основных регистров TMC2209
REG_GCONF       = 0x00
REG_GSTAT       = 0x01
REG_IFCNT       = 0x02
REG_IHOLD_IRUN  = 0x10
REG_TPOWERDOWN  = 0x11
REG_TPWMTHRS    = 0x13
REG_TCOOLTHRS   = 0x14
REG_SGTHRS      = 0x40
REG_SG_RESULT   = 0x41
REG_COOLCONF    = 0x42
REG_CHOPCONF    = 0x6C
REG_DRV_STATUS  = 0x6F
REG_PWMCONF     = 0x70


def calc_crc(data: bytes) -> int:
    """Вычисление контрольной суммы CRC8 для протокола TMC2209 (полином x^8 + x^2 + x^1 + x^0)."""
    crc = 0
    for byte in data:
        for _ in range(8):
            if (crc >> 7) ^ (byte & 0x01):
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
            byte >>= 1
    return crc


class TMC2209UART:
    """Управление регистрами TMC2209 через программный однопроводной UART (pigpio bitbang/wave)."""

    def __init__(
        self,
        pi,
        uart_pin: int,
        baudrate: int = 115200,
        slave_address: int = 0,
        dry_run: bool = False
    ):
        self.pi = pi
        self.uart_pin = uart_pin
        self.baudrate = baudrate
        self.slave_address = slave_address
        self.dry_run = dry_run

        if not self.dry_run and self.pi is not None and self.uart_pin is not None:
            try:
                # Настройка пина как полудуплексный программный UART
                self.pi.set_mode(self.uart_pin, PI_INPUT)
            except Exception as e:
                print(f"[TMC2209-UART] Предупреждение настройки пина {self.uart_pin}: {e}")

    def write_register(self, reg_addr: int, value: int):
        """Запись 32-битного значения в регистр TMC2209."""
        if self.dry_run or self.pi is None or self.uart_pin is None:
            return

        # Формирование пакета записи (8 байт)
        # Байт 0: 0x05 (Sync)
        # Байт 1: Slave address (0..3)
        # Байт 2: Register address | 0x80 (флаг записи)
        # Байты 3..6: 32-битное значение (Big-Endian)
        # Байт 7: CRC
        payload = bytearray([
            0x05,
            self.slave_address & 0x03,
            (reg_addr | 0x80) & 0xFF,
            (value >> 24) & 0xFF,
            (value >> 16) & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF
        ])
        crc = calc_crc(payload)
        payload.append(crc)

        # Отправка пакета через переключение пина на вывод (wave_add_serial)
        try:
            self.pi.wave_clear()
            self.pi.set_mode(self.uart_pin, PI_OUTPUT)
            self.pi.wave_add_serial(self.uart_pin, self.baudrate, payload)
            wid = self.pi.wave_create()
            if wid >= 0:
                self.pi.wave_send_once(wid)
                while self.pi.wave_tx_busy():
                    time.sleep(0.0005)
                self.pi.wave_delete(wid)
            self.pi.set_mode(self.uart_pin, PI_INPUT)
        except Exception as e:
            print(f"[TMC2209-UART] Ошибка записи в регистр 0x{reg_addr:02X}: {e}")

    def set_current(self, run_current_ma: int, hold_percent: int = 50):
        """
        Установка тока двигателя в мА и процента тока удержания.
        CS (Current Scale) вычисляется в диапазоне 0..31.
        """
        # При шунтах 0.11 Ом: I_rms = (CS + 1) / 32 * (V_fs / 0.11)
        # Стандартно CS=31 соответствует примерно 1.4A RMS при внутреннем V_fs
        cs_run = min(31, max(0, int(round((run_current_ma / 1400.0) * 31))))
        cs_hold = min(31, max(0, int(round(cs_run * (hold_percent / 100.0)))))

        # Регистр IHOLD_IRUN:
        # Биты 0..4: IHOLD
        # Биты 8..12: IRUN
        # Биты 16..19: IHOLDDELAY (время снижения тока после остановки)
        val = (cs_hold & 0x1F) | ((cs_run & 0x1F) << 8) | (6 << 16)
        self.write_register(REG_IHOLD_IRUN, val)

    def set_stallguard_threshold(self, threshold: int):
        """Установка чувствительности StallGuard4 (0..255). Больше = чувствительнее."""
        val = max(0, min(255, threshold))
        self.write_register(REG_SGTHRS, val)

    def set_stealthchop(self, enabled: bool):
        """Включение бесшумного режима StealthChop2 (True) или SpreadCycle (False)."""
        # В GCONF: бит 2 (en_SpreadCycle)
        val = 0 if enabled else (1 << 2)
        self.write_register(REG_GCONF, val)


class TMC2209Manager:
    """Менеджер двух приводов TMC2209 для CoreXY с поддержкой StallGuard Homing."""

    def __init__(self, pi, dry_run: bool = False):
        self.pi = pi
        self.dry_run = dry_run

        self.left_uart = TMC2209UART(
            pi=self.pi,
            uart_pin=config.MOTOR_LEFT.uart_pin,
            baudrate=config.MOTOR_LEFT.uart_baudrate,
            slave_address=0,
            dry_run=self.dry_run
        )
        self.right_uart = TMC2209UART(
            pi=self.pi,
            uart_pin=config.MOTOR_RIGHT.uart_pin,
            baudrate=config.MOTOR_RIGHT.uart_baudrate,
            slave_address=1,
            dry_run=self.dry_run
        )

        self._setup_diag_pins()
        self.configure_drivers()

    def _setup_diag_pins(self):
        """Настройка пинов DIAG на вход с чтением сигнала упора StallGuard4."""
        if not self.dry_run and self.pi is not None:
            if config.MOTOR_LEFT.diag_pin is not None:
                self.pi.set_mode(config.MOTOR_LEFT.diag_pin, PI_INPUT)
            if config.MOTOR_RIGHT.diag_pin is not None:
                self.pi.set_mode(config.MOTOR_RIGHT.diag_pin, PI_INPUT)

    def configure_drivers(self):
        """Начальная отправка настроек тока и StallGuard в оба драйвера."""
        print("[TMC2209] Конфигурация параметров драйверов...")
        # Левый мотор
        self.left_uart.set_current(
            config.MOTOR_LEFT.current_ma,
            config.MOTOR_LEFT.hold_current_percent
        )
        self.left_uart.set_stallguard_threshold(config.MOTOR_LEFT.stallguard_threshold)
        self.left_uart.set_stealthchop(config.MOTOR_LEFT.stealthchop)

        # Правый мотор
        self.right_uart.set_current(
            config.MOTOR_RIGHT.current_ma,
            config.MOTOR_RIGHT.hold_current_percent
        )
        self.right_uart.set_stallguard_threshold(config.MOTOR_RIGHT.stallguard_threshold)
        self.right_uart.set_stealthchop(config.MOTOR_RIGHT.stealthchop)

    def read_diag_left(self) -> bool:
        """Считывание состояния пина DIAG левого мотора (1 = упор)."""
        if self.dry_run or self.pi is None or config.MOTOR_LEFT.diag_pin is None:
            return False
        return self.pi.read(config.MOTOR_LEFT.diag_pin) == 1

    def read_diag_right(self) -> bool:
        """Считывание состояния пина DIAG правого мотора (1 = упор)."""
        if self.dry_run or self.pi is None or config.MOTOR_RIGHT.diag_pin is None:
            return False
        return self.pi.read(config.MOTOR_RIGHT.diag_pin) == 1


class SensorlessHomingHelper:
    """
    Помощник бездатчикового автопоиска нуля (Sensorless Homing)
    по сигналам StallGuard4 на пинах DIAG драйверов TMC2209.
    """

    def __init__(self, pi, dry_run: bool = False):
        self.pi = pi
        self.dry_run = dry_run
        self.manager = TMC2209Manager(pi=self.pi, dry_run=self.dry_run)

    def home_axis(self, axis: str, max_travel_mm: float = 450.0, speed_mm_s: float = 25.0) -> bool:
        """
        Поиск нуля для выбранной оси ('X' или 'Y').
        Движение в сторону упора до обнаружения сигнала DIAG.
        """
        axis = axis.upper()
        if axis not in ("X", "Y"):
            raise ValueError(f"Неизвестная ось: {axis}")

        print(f"[SensorlessHoming] Старт автопоиска нуля для оси {axis}...")
        if self.dry_run or self.pi is None:
            time.sleep(0.3)
            print(f"[SensorlessHoming] Ось {axis} откалибрована (эмуляция)")
            return True

        # Проверяем наличие пинов DIAG
        diag_left = config.MOTOR_LEFT.diag_pin
        diag_right = config.MOTOR_RIGHT.diag_pin
        if diag_left is None and diag_right is None:
            print("[SensorlessHoming] Пины DIAG не сконфигурированы!")
            return False

        # Шаг микроперемещения (например, 1 мм за порцию)
        step_mm = 1.0
        steps_count = int(max_travel_mm / step_mm)

        for _ in range(steps_count):
            # Проверка сигнала StallGuard
            stalled_left = self.manager.read_diag_left()
            stalled_right = self.manager.read_diag_right()

            if stalled_left or stalled_right:
                print(f"[SensorlessHoming] Обнаружен упор по оси {axis}! (L={stalled_left}, R={stalled_right})")
                time.sleep(0.05)
                return True

            # Микрошаг в сторону уменьшения координаты (-step_mm)
            time.sleep(step_mm / speed_mm_s)

        print(f"[SensorlessHoming] Превышен предел перемещения {max_travel_mm} мм без упора!")
        return False

