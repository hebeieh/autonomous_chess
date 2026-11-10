"""
Драйвер кинематики CoreXY на базе аппаратных импульсов (волн) библиотеки pigpio.

Использует методы:
- pigpio.pulse(gpio_on, gpio_off, delay_us)
- pi.wave_add_generic()
- pi.wave_create()
- pi.wave_send_once()
- pi.wave_delete()

Поддерживает автоматическое разбиение на чанки при длинных перемещениях
и режим эмуляции для запуска на ПК без Raspberry Pi.
"""

import math
import sys
import time
from typing import List, Optional

try:
    import pigpio
    PI_OUTPUT = pigpio.OUTPUT
    PI_INPUT = pigpio.INPUT
except ImportError:
    pigpio = None
    PI_OUTPUT = 1
    PI_INPUT = 0

import config


# Максимальное количество шагов в одном чанке волны pigpio
# 2500 шагов создают до 5000 импульсов, что безопасно укладывается в буфер pigpio
MAX_STEPS_PER_WAVE_CHUNK = 2500


class BaseWaveController:
    """Базовый интерфейс контроллера движения."""

    def __init__(self):
        self.current_x = 0.0
        self.current_y = 0.0
        self.magnet_active = False

    def enable_motors(self):
        raise NotImplementedError

    def disable_motors(self):
        raise NotImplementedError

    def set_electromagnet(self, state: bool):
        raise NotImplementedError

    def move_cartesian(self, dx_mm: float, dy_mm: float, speed_mm_s: float):
        raise NotImplementedError

    def cleanup(self):
        raise NotImplementedError


class MockWaveController(BaseWaveController):
    """Эмулятор контроллера CoreXY для запуска на ПК или без pigpio."""

    def __init__(self):
        super().__init__()
        print("[MOCK-WAVE] Инициализирован эмулятор аппаратных волн CoreXY")

    def enable_motors(self):
        print("[MOCK-WAVE] Драйверы DRV8825 ВКЛЮЧЕНЫ (обмотки под током)")

    def disable_motors(self):
        print("[MOCK-WAVE] Драйверы DRV8825 ОТКЛЮЧЕНЫ (обмотки обесточены)")

    def set_electromagnet(self, state: bool):
        self.magnet_active = state
        status = "ВКЛЮЧЕН" if state else "ВЫКЛЮЧЕН"
        print(f"[MOCK-WAVE] Электромагнит: {status}")
        time.sleep(0.05)  # Небольшая задержка эмуляции намагничивания

    def move_cartesian(self, dx_mm: float, dy_mm: float, speed_mm_s: float):
        self.current_x += dx_mm
        self.current_y += dy_mm
        dist = math.hypot(dx_mm, dy_mm)
        da = dx_mm + dy_mm
        db = dx_mm - dy_mm
        steps_a = int(round(da * config.STEPS_PER_MM))
        steps_b = int(round(db * config.STEPS_PER_MM))
        travel_time = dist / speed_mm_s if speed_mm_s > 0 else 0.01

        print(f"[MOCK-WAVE] Ход CoreXY: dX={dx_mm:+.1f} мм, dY={dy_mm:+.1f} мм "
              f"(L:{steps_a:+d}, R:{steps_b:+d} шагов, V={speed_mm_s:.1f} мм/с, t={travel_time:.2f}с) "
              f"-> Новая позиция: X={self.current_x:.1f}, Y={self.current_y:.1f}")

        # Быстрая симуляция задержки перемещения (масштабированная для интерактивности)
        sim_delay = min(travel_time * 0.1, 0.15)
        time.sleep(sim_delay)

    def cleanup(self):
        self.set_electromagnet(False)
        self.disable_motors()
        print("[MOCK-WAVE] Очистка ресурсов завершена")


class PigpioWaveController(BaseWaveController):
    """Реализация управления CoreXY через аппаратные волны pigpio (DMA)."""

    def __init__(self):
        super().__init__()
        if pigpio is None:
            raise RuntimeError("Модуль pigpio не установлен!")

        self.pi = pigpio.pi()
        if not self.pi.connected:
            raise ConnectionError("Не удалось подключиться к демону pigpiod! Запустите: sudo pigpiod")

        self.step_mask_left = 1 << config.MOTOR_LEFT.step_pin
        self.step_mask_right = 1 << config.MOTOR_RIGHT.step_pin

        self._setup_gpio()

    def _setup_gpio(self):
        # Настройка выходов моторов
        for pin in [config.MOTOR_LEFT.step_pin, config.MOTOR_LEFT.dir_pin, config.MOTOR_LEFT.enable_pin]:
            self.pi.set_mode(pin, PI_OUTPUT)
            self.pi.write(pin, 0)

        for pin in [config.MOTOR_RIGHT.step_pin, config.MOTOR_RIGHT.dir_pin, config.MOTOR_RIGHT.enable_pin]:
            self.pi.set_mode(pin, PI_OUTPUT)
            self.pi.write(pin, 0)

        # Настройка электромагнита
        self.pi.set_mode(config.ELECTROMAGNET_PIN, PI_OUTPUT)
        self.pi.write(config.ELECTROMAGNET_PIN, 0)

        # Очистка возможных незавершенных волн в памяти pigpio
        self.pi.wave_clear()
        self.disable_motors()

    def enable_motors(self):
        active = config.DRV8825_ENABLE_ACTIVE_STATE
        self.pi.write(config.MOTOR_LEFT.enable_pin, active)
        self.pi.write(config.MOTOR_RIGHT.enable_pin, active)
        time.sleep(0.02)

    def disable_motors(self):
        disabled = 1 if config.DRV8825_ENABLE_ACTIVE_STATE == 0 else 0
        self.pi.write(config.MOTOR_LEFT.enable_pin, disabled)
        self.pi.write(config.MOTOR_RIGHT.enable_pin, disabled)

    def set_electromagnet(self, state: bool):
        self.magnet_active = state
        self.pi.write(config.ELECTROMAGNET_PIN, 1 if state else 0)
        # Пауза на намагничивание/размагничивание
        time.sleep(0.25)

    def move_cartesian(self, dx_mm: float, dy_mm: float, speed_mm_s: float):
        """
        Перемещение каретки на вектор (dx_mm, dy_mm) со скоростью speed_mm_s
        с помощью аппаратных волн pigpio.
        """
        da_mm = dx_mm + dy_mm
        db_mm = dx_mm - dy_mm

        steps_a = int(round(da_mm * config.STEPS_PER_MM))
        steps_b = int(round(db_mm * config.STEPS_PER_MM))

        abs_a = abs(steps_a)
        abs_b = abs(steps_b)
        total_steps = max(abs_a, abs_b)

        if total_steps == 0:
            return

        # Установка пинов направления
        dir_a_val = (steps_a >= 0)
        if config.MOTOR_LEFT.invert_dir:
            dir_a_val = not dir_a_val
        self.pi.write(config.MOTOR_LEFT.dir_pin, 1 if dir_a_val else 0)

        dir_b_val = (steps_b >= 0)
        if config.MOTOR_RIGHT.invert_dir:
            dir_b_val = not dir_b_val
        self.pi.write(config.MOTOR_RIGHT.dir_pin, 1 if dir_b_val else 0)

        # Расчет межимпульсного интервала
        dist = math.hypot(dx_mm, dy_mm)
        if dist == 0:
            dist = total_steps / config.STEPS_PER_MM

        safe_speed = max(speed_mm_s, 1.0)
        total_time_s = dist / safe_speed
        interval_us = int(round((total_time_s / total_steps) * 1_000_000))
        # Ограничение минимального интервала: не быстрее 12 кГц (80 мкс) для надежности
        if interval_us < 80:
            interval_us = 80

        pulse_high_us = min(config.STEP_PULSE_DURATION_US, interval_us // 2)
        pulse_low_us = interval_us - pulse_high_us

        # Разбиение траектории на чанки для непревышения буфера волны
        accum_a = 0
        accum_b = 0
        steps_remaining = total_steps

        while steps_remaining > 0:
            chunk_size = min(steps_remaining, MAX_STEPS_PER_WAVE_CHUNK)
            pulses = []

            for _ in range(chunk_size):
                accum_a += abs_a
                accum_b += abs_b

                mask_on = 0
                if accum_a >= total_steps:
                    accum_a -= total_steps
                    mask_on |= self.step_mask_left

                if accum_b >= total_steps:
                    accum_b -= total_steps
                    mask_on |= self.step_mask_right

                if mask_on:
                    # Импульс высокого уровня
                    pulses.append(pigpio.pulse(mask_on, 0, pulse_high_us))
                    # Пауза низкого уровня
                    pulses.append(pigpio.pulse(0, mask_on, pulse_low_us))
                else:
                    # Холостой такт ожидания
                    pulses.append(pigpio.pulse(0, 0, interval_us))

            # Компиляция и отправка волны
            self.pi.wave_add_generic(pulses)
            wid = self.pi.wave_create()
            if wid < 0:
                raise RuntimeError(f"pigpio wave_create вернул ошибку: {wid}")

            try:
                self.pi.wave_send_once(wid)

                # Ожидание завершения воспроизведения текущей волны
                while self.pi.wave_tx_busy():
                    time.sleep(0.001)
            finally:
                # Гарантированное освобождение памяти волны даже при прерывании
                self.pi.wave_delete(wid)

            steps_remaining -= chunk_size

        self.current_x += dx_mm
        self.current_y += dy_mm

    def cleanup(self):
        self.set_electromagnet(False)
        self.disable_motors()
        if self.pi:
            self.pi.wave_clear()
            self.pi.stop()


def create_wave_controller(dry_run: bool = False) -> BaseWaveController:
    """Фабричная функция создания контроллера с защитой от отсутствия pigpio."""
    if dry_run:
        return MockWaveController()

    try:
        return PigpioWaveController()
    except (ImportError, ConnectionError, RuntimeError) as e:
        print(f"ВНИМАНИЕ: {e}")
        print("Автоматическое переключение в режим эмулятора (MockWaveController)!\n")
        return MockWaveController()
