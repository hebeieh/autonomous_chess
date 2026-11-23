#!/usr/bin/env python3
"""
Скрипт проверки и тестирования шаговых двигателей CoreXY и электромагнита
для проекта автономной шахматной доски.

По умолчанию запускается в интерактивном режиме управления с клавиатуры (WASD + диагонали QEZC).
Для запуска автоматического теста квадрата используйте флаг --auto.

Поддерживает работу через pigpio на Raspberry Pi и режим эмуляции (--dry-run).
"""

import argparse
import select
import sys
import time

try:
    import termios
    import tty
    TERMIOS_AVAILABLE = True
except ImportError:
    TERMIOS_AVAILABLE = False

try:
    import pigpio
    PI_OUTPUT = pigpio.OUTPUT
    PI_INPUT = pigpio.INPUT
except ImportError:
    pigpio = None
    PI_OUTPUT = 1
    PI_INPUT = 0

import config


class MockPigpio:
    """Эмулятор pigpio для отладки на ПК без Raspberry Pi."""

    OUTPUT = 1
    INPUT = 0

    def __init__(self):
        self.connected = True
        self.pin_states = {}
        print("[MOCK] Инициализирован эмулятор GPIO (dry-run режим)")

    def set_mode(self, pin: int, mode: int):
        pass

    def write(self, pin: int, level: int):
        self.pin_states[pin] = level

    def read(self, pin: int) -> int:
        return self.pin_states.get(pin, 0)

    def stop(self):
        print("[MOCK] Эмулятор остановлен, ресурсы освобождены")


class CoreXYController:
    """Управление шаговыми моторами по схеме CoreXY и электромагнитом."""

    def __init__(self, dry_run: bool = False, soft_limits: bool = True):
        self.dry_run = dry_run
        self.soft_limits = soft_limits
        self.pi = None
        self.pos_x = 0.0
        self.pos_y = 0.0
        self.magnet_state = False
        self.motors_enabled = False

        self._init_gpio()
        self._setup_pins()

    def _init_gpio(self):
        if not self.dry_run:
            if pigpio is not None:
                self.pi = pigpio.pi()
                if not self.pi.connected:
                    print("ВНИМАНИЕ: Не удалось подключиться к pigpiod (демон не запущен).")
                    print("Автоматическое переключение в режим эмуляции (--dry-run).")
                    print("Для работы на RPi выполните: sudo pigpiod\n")
                    self.dry_run = True
                    self.pi = MockPigpio()
            else:
                print("ВНИМАНИЕ: Модуль 'pigpio' не установлен в системе.")
                print("Автоматическое переключение в режим эмуляции (--dry-run).\n")
                self.dry_run = True
                self.pi = MockPigpio()
        else:
            self.pi = MockPigpio()

    def _setup_pins(self):
        # Настройка пинов левого мотора
        for pin in [config.MOTOR_LEFT.step_pin, config.MOTOR_LEFT.dir_pin, config.MOTOR_LEFT.enable_pin]:
            self.pi.set_mode(pin, PI_OUTPUT)
            self.pi.write(pin, 0)

        # Настройка пинов правого мотора
        for pin in [config.MOTOR_RIGHT.step_pin, config.MOTOR_RIGHT.dir_pin, config.MOTOR_RIGHT.enable_pin]:
            self.pi.set_mode(pin, PI_OUTPUT)
            self.pi.write(pin, 0)

        # Настройка пина электромагнита
        self.pi.set_mode(config.ELECTROMAGNET_PIN, PI_OUTPUT)
        self.pi.write(config.ELECTROMAGNET_PIN, 0)

        # По умолчанию отключаем драйверы
        self.disable_motors()

    def enable_motors(self):
        """Включить питание обмоток моторов (Enable = LOW для DRV8825)."""
        active_state = config.DRV8825_ENABLE_ACTIVE_STATE  # 0
        self.pi.write(config.MOTOR_LEFT.enable_pin, active_state)
        self.pi.write(config.MOTOR_RIGHT.enable_pin, active_state)
        self.motors_enabled = True
        time.sleep(0.05)
        print("-> Драйверы DRV8825 ВКЛЮЧЕНЫ (обмотки под током)")

    def disable_motors(self):
        """Обесточить обмотки моторов (Enable = HIGH для DRV8825)."""
        disabled_state = 1 if config.DRV8825_ENABLE_ACTIVE_STATE == 0 else 0
        self.pi.write(config.MOTOR_LEFT.enable_pin, disabled_state)
        self.pi.write(config.MOTOR_RIGHT.enable_pin, disabled_state)
        self.motors_enabled = False
        print("-> Драйверы DRV8825 ОТКЛЮЧЕНЫ (обмотки обесточены)")

    def set_electromagnet(self, state: bool):
        """Включить (True) или выключить (False) электромагнит через IRLZ44N."""
        self.magnet_state = state
        level = 1 if state else 0
        self.pi.write(config.ELECTROMAGNET_PIN, level)
        status_str = "ВКЛЮЧЕН" if state else "ВЫКЛЮЧЕН"
        print(f"-> Электромагнит (GPIO {config.ELECTROMAGNET_PIN}): {status_str}")

    def _step_pulse(self, step_left: bool, step_right: bool):
        """Сгенерировать один шаг на выбранных моторах."""
        if step_left:
            self.pi.write(config.MOTOR_LEFT.step_pin, 1)
        if step_right:
            self.pi.write(config.MOTOR_RIGHT.step_pin, 1)

        # Длительность импульса STEP
        if not self.dry_run:
            # Микросекундная задержка для DRV8825 (мин. 1.9 мкс)
            time.sleep(config.STEP_PULSE_DURATION_US / 1_000_000.0)

        if step_left:
            self.pi.write(config.MOTOR_LEFT.step_pin, 0)
        if step_right:
            self.pi.write(config.MOTOR_RIGHT.step_pin, 0)

    def move_single_motor(self, motor_name: str, distance_mm: float, speed_mm_s: float):
        """
        Тестовое вращение одного отдельного мотора ('left' или 'right').
        Позволяет проверить направление вращения и механику одного привода.
        """
        is_left = motor_name.lower() == 'left'
        cfg = config.MOTOR_LEFT if is_left else config.MOTOR_RIGHT

        steps_total = int(round(abs(distance_mm) * config.STEPS_PER_MM))
        if steps_total == 0:
            return

        # Направление вращения
        forward = (distance_mm > 0)
        if cfg.invert_dir:
            forward = not forward
        dir_val = 1 if forward else 0
        self.pi.write(cfg.dir_pin, dir_val)

        # Интервал между шагами при постоянной скорости
        step_delay = 1.0 / (speed_mm_s * config.STEPS_PER_MM)

        side_str = "ЛЕВЫЙ (Motor A)" if is_left else "ПРАВЫЙ (Motor B)"
        print(f"Вращение: {side_str} на {distance_mm:+.1f} мм ({steps_total} шагов, скорость {speed_mm_s:.1f} мм/с)...")

        t_start = time.time()
        for i in range(steps_total):
            self._step_pulse(step_left=is_left, step_right=not is_left)
            if not self.dry_run:
                time.sleep(step_delay)
            elif i % 200 == 0:
                time.sleep(0.01)

        t_elapsed = time.time() - t_start
        print(f"  Выполнено за {t_elapsed:.2f} сек")

    def move_cartesian(self, dx_mm: float, dy_mm: float, speed_mm_s: float):
        """
        Перемещение каретки в декартовых координатах (X, Y) по формулам CoreXY:
        Delta_A (мотор L) = Delta_X + Delta_Y
        Delta_B (мотор R) = Delta_X - Delta_Y
        """
        # Проверка программных концевиков (Soft Limits)
        if self.soft_limits:
            new_x = self.pos_x + dx_mm
            new_y = self.pos_y + dy_mm
            margin = 0.5  # Допустимая погрешность в мм
            if (new_x < -margin or new_x > config.WORK_AREA_X_MM + margin or
                new_y < -margin or new_y > config.WORK_AREA_Y_MM + margin):
                print(f"[SOFT LIMIT] Движение заблокировано! Цель (X={new_x:.1f}, Y={new_y:.1f}) мм "
                      f"выходит за границы поля [0..{config.WORK_AREA_X_MM:.0f}] x [0..{config.WORK_AREA_Y_MM:.0f}] мм")
                return False

        # Кинематика CoreXY
        da_mm = dx_mm + dy_mm
        db_mm = dx_mm - dy_mm

        steps_a = int(round(da_mm * config.STEPS_PER_MM))
        steps_b = int(round(db_mm * config.STEPS_PER_MM))

        abs_steps_a = abs(steps_a)
        abs_steps_b = abs(steps_b)

        max_steps = max(abs_steps_a, abs_steps_b)
        if max_steps == 0:
            return True

        # Установка направления для мотора A (Left)
        dir_a = (steps_a >= 0)
        if config.MOTOR_LEFT.invert_dir:
            dir_a = not dir_a
        self.pi.write(config.MOTOR_LEFT.dir_pin, 1 if dir_a else 0)

        # Установка направления для мотора B (Right)
        dir_b = (steps_b >= 0)
        if config.MOTOR_RIGHT.invert_dir:
            dir_b = not dir_b
        self.pi.write(config.MOTOR_RIGHT.dir_pin, 1 if dir_b else 0)

        # Расчет времени шага по линейному расстоянию перемещения
        dist_cartesian = (dx_mm ** 2 + dy_mm ** 2) ** 0.5
        total_time = dist_cartesian / speed_mm_s if speed_mm_s > 0 else 0.001
        step_delay = total_time / max_steps if max_steps > 0 else 0.001

        # Минимальный безопасный интервал (не быстрее 10 кГц)
        if step_delay < 0.0001:
            step_delay = 0.0001

        print(f"Перемещение CoreXY: dX={dx_mm:+.1f} мм, dY={dy_mm:+.1f} мм "
              f"(Шаги L:{steps_a:+d}, R:{steps_b:+d}, t={total_time:.2f}с)...")

        # Алгоритм синхронной интерполяции шагов (Bresenham)
        accum_a = 0
        accum_b = 0

        t_start = time.time()
        for i in range(max_steps):
            accum_a += abs_steps_a
            accum_b += abs_steps_b

            step_left = False
            step_right = False

            if accum_a >= max_steps:
                accum_a -= max_steps
                step_left = True

            if accum_b >= max_steps:
                accum_b -= max_steps
                step_right = True

            self._step_pulse(step_left=step_left, step_right=step_right)

            if not self.dry_run:
                time.sleep(step_delay)
            elif i % 500 == 0:
                time.sleep(0.01)

        t_elapsed = time.time() - t_start
        self.pos_x += dx_mm
        self.pos_y += dy_mm
        print(f"  Выполнено за {t_elapsed:.2f} сек. Текущая позиция: X={self.pos_x:.1f} мм, Y={self.pos_y:.1f} мм")
        return True

    def cleanup(self):
        """Очистка ресурсов и обесточивание драйверов."""
        self.set_electromagnet(False)
        self.disable_motors()
        if self.pi:
            self.pi.stop()


def get_terminal_char() -> str:
    """Считывает одно нажатие клавиши в терминале без ожидания Enter."""
    if not TERMIOS_AVAILABLE or not sys.stdin.isatty():
        # Если stdin не является терминалом (например, пайп или автотест)
        line = sys.stdin.readline()
        return line.strip().lower() if line else 'esc'

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)

        # Обработка Escape-последовательностей (стрелки клавиатуры: ESC [ A/B/C/D)
        if ch == '\x1b':
            r, _, _ = select.select([sys.stdin], [], [], 0.05)
            if r:
                seq = sys.stdin.read(2)
                if seq == '[A': return 'w'  # Стрелка вверх
                elif seq == '[B': return 's'  # Стрелка вниз
                elif seq == '[C': return 'd'  # Стрелка вправо
                elif seq == '[D': return 'a'  # Стрелка влево
            return 'esc'

        # Ctrl+C
        if ch == '\x03':
            return 'esc'

        return ch.lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def run_interactive(args):
    """Интерактивный режим управления кареткой CoreXY с клавиатуры в реальном времени."""
    step_mm = args.step_size

    print("=" * 70)
    print("   ИНТЕРАКТИВНОЕ УПРАВЛЕНИЕ COREXY С КЛАВИАТУРЫ В РЕАЛЬНОМ ВРЕМЕНИ")
    print("=" * 70)
    print(f"Параметры:")
    print(f" - Режим:          {'[ЭМУЛЯЦИЯ / DRY-RUN]' if args.dry_run else '[РЕАЛЬНОЕ ЖЕЛЕЗО / PIGPIO]'}")
    print(f" - Размер шага:    {step_mm:.1f} мм (1 шахматная клетка)")
    print(f" - Скорость:       {args.speed:.1f} мм/с")
    print(f" - Границы (Soft): [0..{config.WORK_AREA_X_MM:.0f}] x [0..{config.WORK_AREA_Y_MM:.0f}] мм "
          f"({'ВКЛ' if not args.no_soft_limits else 'ВЫКЛ'})")
    print("=" * 70)
    print("Клавиши управления (срабатывают сразу, БЕЗ нажатия Enter):")
    print("        [ Q ] Вверх-Влево      [ W / ↑ ] Вверх        [ E ] Вверх-Вправо")
    print("        [ A / ← ] Влево        [ S / ↓ ] Вниз         [ D / → ] Вправо")
    print("        [ Z ] Вниз-Влево                              [ C ] Вниз-Вправо")
    print()
    print("Дополнительные команды:")
    print(" [ ПРОБЕЛ / M ] - Включить / выключить электромагнит (IRLZ44N)")
    print(" [ P ]          - Включить / отключить питание моторов (Enable)")
    print(" [ H ]          - Вернуться в исходную точку (0, 0)")
    print(" [ ESC / Ctrl+C]- Завершить работу и выйти")
    print("=" * 70)
    print("ВАЖНО: Перед стартом вручную установите каретку в левый верхний угол (0,0)!\n")

    controller = CoreXYController(
        dry_run=args.dry_run,
        soft_limits=not args.no_soft_limits
    )

    try:
        controller.enable_motors()
        print(f"\n[ГОТОВ] Текущая позиция: X={controller.pos_x:.1f} мм, Y={controller.pos_y:.1f} мм. Жду нажатия клавиши...")

        while True:
            key = get_terminal_char()

            if key in ('esc', 'k', '\x1b', 'quit'):
                print("\n[ВЫХОД] Завершение интерактивного режима...")
                break

            elif key == 'w':
                print(f"\n[ВВЕРХ (-Y {step_mm} мм)]")
                controller.move_cartesian(dx_mm=0.0, dy_mm=-step_mm, speed_mm_s=args.speed)

            elif key == 's':
                print(f"\n[ВНИЗ (+Y {step_mm} мм)]")
                controller.move_cartesian(dx_mm=0.0, dy_mm=step_mm, speed_mm_s=args.speed)

            elif key == 'a':
                print(f"\n[ВЛЕВО (-X {step_mm} мм)]")
                controller.move_cartesian(dx_mm=-step_mm, dy_mm=0.0, speed_mm_s=args.speed)

            elif key == 'd':
                print(f"\n[ВПРАВО (+X {step_mm} мм)]")
                controller.move_cartesian(dx_mm=step_mm, dy_mm=0.0, speed_mm_s=args.speed)

            # Диагональные перемещения (в схеме CoreXY вращается только один мотор!)
            elif key == 'q':
                print(f"\n[ДИАГОНАЛЬ ВВЕРХ-ВЛЕВО (-X, -Y {step_mm} мм)]")
                controller.move_cartesian(dx_mm=-step_mm, dy_mm=-step_mm, speed_mm_s=args.speed)

            elif key == 'e':
                print(f"\n[ДИАГОНАЛЬ ВВЕРХ-ВПРАВО (+X, -Y {step_mm} мм)]")
                controller.move_cartesian(dx_mm=step_mm, dy_mm=-step_mm, speed_mm_s=args.speed)

            elif key == 'z':
                print(f"\n[ДИАГОНАЛЬ ВНИЗ-ВЛЕВО (-X, +Y {step_mm} мм)]")
                controller.move_cartesian(dx_mm=-step_mm, dy_mm=step_mm, speed_mm_s=args.speed)

            elif key == 'c':
                print(f"\n[ДИАГОНАЛЬ ВНИЗ-ВПРАВО (+X, +Y {step_mm} мм)]")
                controller.move_cartesian(dx_mm=step_mm, dy_mm=step_mm, speed_mm_s=args.speed)

            # Управление электромагнитом
            elif key in (' ', 'm'):
                new_state = not controller.magnet_state
                controller.set_electromagnet(new_state)

            # Включение/отключение обмоток моторов
            elif key == 'p':
                if controller.motors_enabled:
                    controller.disable_motors()
                else:
                    controller.enable_motors()

            # Возврат в нулевую точку
            elif key == 'h':
                print("\n[ВОЗВРАТ В НОЛЬ (0, 0)]")
                dx = -controller.pos_x
                dy = -controller.pos_y
                if abs(dx) > 0.1 or abs(dy) > 0.1:
                    controller.move_cartesian(dx_mm=dx, dy_mm=dy, speed_mm_s=args.speed)
                else:
                    print("Каретка уже находится в нулевой точке.")

            else:
                # Игнорировать непредусмотренные символы
                pass

    except KeyboardInterrupt:
        print("\n\nПрерывание пользователем (Ctrl+C)...")
    finally:
        print("\nОчистка ресурсов и отключение питания моторов...")
        controller.cleanup()
        print("Готово.")


def run_test(args):
    """Автоматический тест одиночных моторов, контура квадрата и электромагнита."""
    print("=" * 65)
    print("  АВТОМАТИЧЕСКИЙ ТЕСТ КИНЕМАТИКИ COREXY И ЭЛЕКТРОМАГНИТА")
    print("=" * 65)
    print(f"Параметры:")
    print(f" - Мотор L: STEP={config.MOTOR_LEFT.step_pin}, DIR={config.MOTOR_LEFT.dir_pin}, EN={config.MOTOR_LEFT.enable_pin}")
    print(f" - Мотор R: STEP={config.MOTOR_RIGHT.step_pin}, DIR={config.MOTOR_RIGHT.dir_pin}, EN={config.MOTOR_RIGHT.enable_pin}")
    print(f" - Магнит:  GPIO={config.ELECTROMAGNET_PIN}")
    print(f" - Шагов/мм: {config.STEPS_PER_MM} (микрошаг 1/{config.MICROSTEPPING}, шкив {config.PULLEY_TEETH}T GT2)")
    print(f" - Скорость теста: {args.speed} мм/с")
    print(f" - Размер тестового квадрата: {args.distance} мм")
    print("=" * 65)
    print("ВНИМАНИЕ: Перед запуском убедитесь, что каретка вручную выставлена")
    print("в верхний левый угол (0,0) и имеет запас хода не менее 60 мм по X и Y!")
    print("=" * 65)

    controller = CoreXYController(dry_run=args.dry_run, soft_limits=not args.no_soft_limits)

    try:
        # Шаг 1. Включение моторов
        print("\n--- Шаг 1: Активация драйверов моторов ---")
        controller.enable_motors()
        time.sleep(0.5)

        # Шаг 2. Одиночные тесты моторов (диагональное движение одного привода)
        if not args.skip_singles:
            print("\n--- Шаг 2: Проверка мотора L (Left) ---")
            controller.move_single_motor('left', distance_mm=10.0, speed_mm_s=args.speed)
            time.sleep(0.3)
            controller.move_single_motor('left', distance_mm=-10.0, speed_mm_s=args.speed)
            time.sleep(0.5)

            print("\n--- Шаг 3: Проверка мотора R (Right) ---")
            controller.move_single_motor('right', distance_mm=10.0, speed_mm_s=args.speed)
            time.sleep(0.3)
            controller.move_single_motor('right', distance_mm=-10.0, speed_mm_s=args.speed)
            time.sleep(0.5)

        # Шаг 3. Декартово движение CoreXY по замкнутому контуру (квадрат)
        d = args.distance
        print(f"\n--- Шаг 4: Движение по квадрату CoreXY ({d:.0f}x{d:.0f} мм) ---")
        print(" 1/4: Движение вправо (+X)")
        controller.move_cartesian(dx_mm=d, dy_mm=0.0, speed_mm_s=args.speed)
        time.sleep(0.3)

        print(" 2/4: Движение вниз (+Y, по направлению к моторам)")
        controller.move_cartesian(dx_mm=0.0, dy_mm=d, speed_mm_s=args.speed)
        time.sleep(0.3)

        print(" 3/4: Движение влево (-X)")
        controller.move_cartesian(dx_mm=-d, dy_mm=0.0, speed_mm_s=args.speed)
        time.sleep(0.3)

        print(" 4/4: Движение вверх (-Y, возврат в исходную точку 0,0)")
        controller.move_cartesian(dx_mm=0.0, dy_mm=-d, speed_mm_s=args.speed)
        time.sleep(0.5)

        # Шаг 4. Проверка электромагнита
        if not args.skip_magnet:
            print("\n--- Шаг 5: Проверка электромагнита (IRLZ44N) ---")
            controller.set_electromagnet(True)
            print("Магнит включен на 1.5 секунды...")
            time.sleep(1.5)
            controller.set_electromagnet(False)
            time.sleep(0.5)

        print("\n" + "=" * 65)
        print("  ТЕСТИРОВАНИЕ УСПЕШНО ЗАВЕРШЕНО!")
        print("=" * 65)

    except KeyboardInterrupt:
        print("\n\nПрерывание пользователем (Ctrl+C). Экстренная остановка...")
    finally:
        print("\nОчистка ресурсов и отключение питания моторов...")
        controller.cleanup()
        print("Готово.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description="Тестирование и ручное интерактивное управление моторами CoreXY"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Режим эмуляции без взаимодействия с реальным GPIO'
    )
    parser.add_argument(
        '--auto',
        action='store_true',
        help='Запустить автоматический тест квадрата вместо интерактивного режима'
    )
    parser.add_argument(
        '--step-size',
        type=float,
        default=config.SQUARE_SIZE_MM,
        help=f'Дистанция одного шага в интерактивном режиме в мм (по умолчанию {config.SQUARE_SIZE_MM} мм / 1 клетка)'
    )
    parser.add_argument(
        '--speed',
        type=float,
        default=config.DEFAULT_TEST_SPEED_MM_S,
        help=f'Скорость перемещения в мм/с (по умолчанию {config.DEFAULT_TEST_SPEED_MM_S})'
    )
    parser.add_argument(
        '--distance',
        type=float,
        default=50.0,
        help='Длина стороны квадрата для автоматического теста в мм (по умолчанию 50.0)'
    )
    parser.add_argument(
        '--no-soft-limits',
        action='store_true',
        help='Отключить программные ограничения перемещения по границам стола'
    )
    parser.add_argument(
        '--skip-singles',
        action='store_true',
        help='(Для режима --auto) Пропустить проверку одиночных моторов'
    )
    parser.add_argument(
        '--skip-magnet',
        action='store_true',
        help='(Для режима --auto) Пропустить проверку электромагнита'
    )

    args = parser.parse_args()

    if args.auto:
        run_test(args)
    else:
        run_interactive(args)
