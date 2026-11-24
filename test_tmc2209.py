#!/usr/bin/env python3
"""
Консольная утилита для комплексного тестирования драйверов TMC2209
на двухосевой механике CoreXY шахматного стола.

Возможности:
1. Тест одиночных моторов (Left / Right) вперед-назад.
2. Интерактивное управление CoreXY с клавиатуры (WASD + диагонали QEZC).
3. Мониторинг пинов StallGuard4 DIAG (GPIO 4 и GPIO 5) в реальном времени.
4. Тест бездатчикового автопоиска нуля (Sensorless Homing).
5. Настройка параметров TMC2209 по UART (ток, StealthChop, чувствительность SGTHRS).
6. Тест электромагнита (IRLZ44N на GPIO 18).
7. Тест удержания обмоток (Enable/Disable).

Использование:
    python3 test_tmc2209.py            # Интерактивное меню
    python3 test_tmc2209.py --jog      # Сразу перейти в ручное управление WASD
    python3 test_tmc2209.py --diag     # Запуск живого монитора сигналов DIAG
    python3 test_tmc2209.py --homing   # Тест StallGuard Homing
    python3 test_tmc2209.py --auto     # Автоматический тест квадрата 50х50 мм
    python3 test_tmc2209.py --dry-run  # Режим эмуляции (без подключения к GPIO)
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
from src.corexy_wave import BaseWaveController, MockWaveController, PigpioWaveController, create_wave_controller
from src.tmc2209_driver import TMC2209Manager, SensorlessHomingHelper


# ANSI цветовые коды
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_GREEN = "\033[92m"
C_CYAN = "\033[96m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_DIM = "\033[90m"


class TMC2209CLITester:
    """Инструмент комплексной проверки драйверов TMC2209."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.pi = None
        self.controller = None
        self.tmc_manager = None
        self.homing_helper = None
        
        self.current_step_mm = 10.0
        self.speed_mm_s = getattr(config, "DEFAULT_TEST_SPEED_MM_S", 25.0)

        self._init_hardware()

    def _init_hardware(self):
        """Подключение к pigpiod и инициализация контроллера."""
        if not self.dry_run:
            if pigpio is not None:
                self.pi = pigpio.pi()
                if not self.pi.connected:
                    print(f"{C_YELLOW}[ВНИМАНИЕ]{C_RESET} Демон pigpiod не запущен.")
                    print("Запустите в соседнем терминале: sudo pigpiod")
                    print("Переключение в режим эмуляции (--dry-run)...\n")
                    self.dry_run = True
                    self.pi = None
            else:
                print(f"{C_YELLOW}[ВНИМАНИЕ]{C_RESET} Модуль pigpio не найден.")
                self.dry_run = True

        self.controller = create_wave_controller(dry_run=self.dry_run)
        self.tmc_manager = TMC2209Manager(pi=self.pi, dry_run=self.dry_run)
        self.homing_helper = SensorlessHomingHelper(pi=self.pi, dry_run=self.dry_run)

    def test_single_motor(self, motor_name: str, distance_mm: float = 20.0):
        """Вращение одного мотора вперед и назад."""
        is_left = motor_name.lower() == "left"
        side_str = "ЛЕВЫЙ (Motor A)" if is_left else "ПРАВЫЙ (Motor B)"
        cfg = config.MOTOR_LEFT if is_left else config.MOTOR_RIGHT

        print(f"\n{C_BOLD}=== Тестирование: {side_str} ==={C_RESET}")
        print(f"Пины: STEP={cfg.step_pin}, DIR={cfg.dir_pin}, EN={cfg.enable_pin}")
        print(f"Дистанция: {distance_mm} мм в обе стороны со скоростью {self.speed_mm_s} мм/с")

        self.controller.enable_motors()

        # Вращение вперед
        dx = distance_mm if is_left else distance_mm
        dy = distance_mm if is_left else -distance_mm
        print(f"-> Вращение вперед (+{distance_mm} мм)...")
        self.controller.move_cartesian(dx, dy, self.speed_mm_s)
        time.sleep(0.3)

        # Вращение назад
        print(f"-> Вращение назад (-{distance_mm} мм)...")
        self.controller.move_cartesian(-dx, -dy, self.speed_mm_s)
        time.sleep(0.3)

        print(f"{C_GREEN}✓ Тест мотора {side_str} успешно завершен!{C_RESET}")

    def monitor_diag_pins(self):
        """Мониторинг состояния пинов DIAG (StallGuard4) в реальном времени."""
        pin_l = config.MOTOR_LEFT.diag_pin
        pin_r = config.MOTOR_RIGHT.diag_pin

        print(f"\n{C_BOLD}{C_CYAN}=== ЖИВОЙ МОНИТОРИНГ ПИНОВ STALLGUARD DIAG ==={C_RESET}")
        print(f"Левый мотор:  GPIO {pin_l}")
        print(f"Правый мотор: GPIO {pin_r}")
        print("0 = Нормальное движение | 1 = ОБНАРУЖЕН УПОР (STALL)")
        print(f"{C_YELLOW}Попробуйте вручную заблокировать или сдвинуть каретку.{C_RESET}")
        print(f"{C_DIM}Нажмите Ctrl+C для возврата в меню...{C_RESET}\n")

        try:
            while True:
                diag_l = 1 if self.tmc_manager.read_diag_left() else 0
                diag_r = 1 if self.tmc_manager.read_diag_right() else 0

                status_l = f"{C_RED}{C_BOLD}[STALL!]{C_RESET}" if diag_l else f"{C_GREEN}[ OK ]{C_RESET}"
                status_r = f"{C_RED}{C_BOLD}[STALL!]{C_RESET}" if diag_r else f"{C_GREEN}[ OK ]{C_RESET}"

                sys.stdout.write(f"\rDIAG Левый (GPIO {pin_l}): {diag_l} {status_l}  |  DIAG Правый (GPIO {pin_r}): {diag_r} {status_r}   ")
                sys.stdout.flush()
                time.sleep(0.05)
        except KeyboardInterrupt:
            print(f"\n{C_CYAN}Мониторинг остановлен.{C_RESET}\n")

    def test_homing(self):
        """Тест бездатчикового автопоиска нуля."""
        print(f"\n{C_BOLD}{C_CYAN}=== ТЕСТ БЕЗДАТЧИКОВОГО АВТОПОИСКА НУЛЯ (STALLGUARD) ==={C_RESET}")
        print("Каретка будет медленно двигаться к упорам по осям X и Y.")
        print("При касании рамы сигнал DIAG остановит двигатель.\n")

        confirm = input("Начать автопоиск нуля? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return

        self.controller.enable_motors()
        print("\n1. Калибровка оси X...")
        res_x = self.homing_helper.home_axis("X")
        time.sleep(0.5)

        print("\n2. Калибровка оси Y...")
        res_y = self.homing_helper.home_axis("Y")
        time.sleep(0.5)

        self.controller.current_x = 0.0
        self.controller.current_y = 0.0

        if res_x and res_y:
            print(f"\n{C_GREEN}{C_BOLD}✓ StallGuard Homing успешно завершен! Позиция: X=0.0, Y=0.0{C_RESET}")
        else:
            print(f"\n{C_YELLOW}Внимание: Homing завершен частично (X={res_x}, Y={res_y}){C_RESET}")

    def configure_uart_interactive(self):
        """Интерактивная настройка параметров драйверов по UART."""
        print(f"\n{C_BOLD}=== НАСТРОЙКА TMC2209 ПО UART ==={C_RESET}")
        print(f"1. Текущий ток (RMS): {config.MOTOR_LEFT.current_ma} мА")
        print(f"2. StealthChop: {'ВКЛ (Бесшумный)' if config.MOTOR_LEFT.stealthchop else 'ВЫКЛ (SpreadCycle)'}")
        print(f"3. Чувствительность StallGuard SGTHRS: {config.MOTOR_LEFT.stallguard_threshold} (0..255)")
        print("-" * 50)

        try:
            curr_str = input(f"Введите новый рабочий ток в мА [Enter для сохранения {config.MOTOR_LEFT.current_ma}]: ").strip()
            if curr_str:
                new_current = int(curr_str)
                self.tmc_manager.left_uart.set_current(new_current)
                self.tmc_manager.right_uart.set_current(new_current)
                print(f"{C_GREEN}✓ Установлен ток {new_current} мА{C_RESET}")

            sg_str = input(f"Введите порог StallGuard SGTHRS (0..255) [Enter для сохранения {config.MOTOR_LEFT.stallguard_threshold}]: ").strip()
            if sg_str:
                new_sg = int(sg_str)
                self.tmc_manager.left_uart.set_stallguard_threshold(new_sg)
                self.tmc_manager.right_uart.set_stallguard_threshold(new_sg)
                print(f"{C_GREEN}✓ Установлен порог SGTHRS {new_sg}{C_RESET}")

            sc_str = input("Режим StealthChop? (1 = Бесшумный, 0 = Мощный SpreadCycle) [Enter пропустить]: ").strip()
            if sc_str in ("0", "1"):
                sc_mode = (sc_str == "1")
                self.tmc_manager.left_uart.set_stealthchop(sc_mode)
                self.tmc_manager.right_uart.set_stealthchop(sc_mode)
                print(f"{C_GREEN}✓ StealthChop: {sc_mode}{C_RESET}")

        except ValueError as e:
            print(f"{C_RED}Ошибка ввода числа: {e}{C_RESET}")

    def test_magnet(self):
        """Тест включения и выключения электромагнита."""
        print(f"\n{C_BOLD}=== ТЕСТ ЭЛЕКТРОМАГНИТА (GPIO {config.ELECTROMAGNET_PIN}) ==={C_RESET}")
        print("Включение электромагнита на 2 секунды...")
        self.controller.set_electromagnet(True)
        time.sleep(2.0)
        self.controller.set_electromagnet(False)
        print(f"{C_GREEN}✓ Электромагнит выключен. Тест завершен.{C_RESET}")

    def toggle_power(self):
        """Включение или отключение питания обмоток моторов."""
        if self.controller.motors_enabled if hasattr(self.controller, "motors_enabled") else False:
            self.controller.disable_motors()
            print(f"{C_YELLOW}-> Обмотки моторов ОБЕСТОЧЕНЫ (вал вращается свободно){C_RESET}")
        else:
            self.controller.enable_motors()
            print(f"{C_GREEN}-> Обмотки моторов ПОД ТОКОМ (удержание включено){C_RESET}")

    def test_square_auto(self, size_mm: float = 50.0):
        """Автоматический тест движения по контуру квадрата."""
        print(f"\n{C_BOLD}=== АВТО-ТЕСТ ДВИЖЕНИЯ ПО КВАДРАТУ ({size_mm}x{size_mm} мм) ==={C_RESET}")
        print("Убедитесь, что каретка имеет запас хода минимум 60 мм вправо (+X) и вниз (+Y)!")
        confirm = input("Начать? [y/N]: ").strip().lower()
        if confirm not in ("y", "yes", "д", "да"):
            return

        self.controller.enable_motors()
        
        # 1. Вправо
        print(f"1/4: Движение вправо (+{size_mm} мм X)...")
        self.controller.move_cartesian(size_mm, 0, self.speed_mm_s)
        time.sleep(0.3)

        # 2. Вниз
        print(f"2/4: Движение вниз (+{size_mm} мм Y)...")
        self.controller.move_cartesian(0, size_mm, self.speed_mm_s)
        time.sleep(0.3)

        # 3. Влево
        print(f"3/4: Движение влево (-{size_mm} мм X)...")
        self.controller.move_cartesian(-size_mm, 0, self.speed_mm_s)
        time.sleep(0.3)

        # 4. Вверх
        print(f"4/4: Возврат вверх (-{size_mm} мм Y)...")
        self.controller.move_cartesian(0, -size_mm, self.speed_mm_s)
        time.sleep(0.3)

        print(f"{C_GREEN}✓ Движение по квадрату успешно завершено!{C_RESET}")

    def interactive_jog(self):
        """Интерактивное управление CoreXY в реальном времени с клавиатуры."""
        if not TERMIOS_AVAILABLE:
            print(f"{C_RED}Ошибка: интерактивный ввод termios не поддерживается на данной платформе.{C_RESET}")
            return

        print(f"\n{C_BOLD}{C_CYAN}=== ИНТЕРАКТИВНОЕ УПРАВЛЕНИЕ COREXY (TMC2209) ==={C_RESET}")
        print("  Навигация:     [W/A/S/D] — ортогонально (Вверх / Влево / Вниз / Вправо)")
        print("  Диагонали:     [Q/E/Z/C] — диагональные перемещения")
        print("  Шаг хода:      [1] = 1 мм | [2] = 5 мм | [3] = 10 мм | [4] = 34 мм (клетка)")
        print("  Электромагнит: [Space]   — вкл/выкл магнит")
        print("  Питание:       [P]       — вкл/выкл питание обмоток")
        print("  Калибровка:    [H]       — StallGuard Homing")
        print(f"  Выход:         {C_YELLOW}[ESC]{C_RESET} или {C_YELLOW}[X]{C_RESET}\n")

        self.controller.enable_motors()

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        try:
            tty.setraw(fd)
            while True:
                # Чтение нажатия клавиши
                rlist, _, _ = select.select([sys.stdin], [], [], 0.1)
                if not rlist:
                    continue

                ch = sys.stdin.read(1)

                # Обработка ESC последовательностей
                if ch == '\x1b':
                    # Проверяем, это одиночный ESC или стрелка
                    r2, _, _ = select.select([sys.stdin], [], [], 0.05)
                    if not r2:
                        break  # Нажат одиночный ESC — выход
                    seq = sys.stdin.read(2)
                    if seq == '[A': ch = 'w'
                    elif seq == '[B': ch = 's'
                    elif seq == '[C': ch = 'd'
                    elif seq == '[D': ch = 'a'
                    else: continue

                ch_lower = ch.lower()

                if ch_lower in ('x', '\x03'):  # 'x' или Ctrl+C
                    break

                # Выбор шага
                if ch == '1':
                    self.current_step_mm = 1.0
                elif ch == '2':
                    self.current_step_mm = 5.0
                elif ch == '3':
                    self.current_step_mm = 10.0
                elif ch == '4':
                    self.current_step_mm = getattr(config, "SQUARE_SIZE_MM", 34.0)

                # Перемещение
                dx = 0.0
                dy = 0.0
                if ch_lower in ('w', 'ц'): dy = -self.current_step_mm   # Вверх
                elif ch_lower in ('s', 'ы'): dy = self.current_step_mm  # Вниз
                elif ch_lower in ('a', 'ф'): dx = -self.current_step_mm # Влево
                elif ch_lower in ('d', 'в'): dx = self.current_step_mm  # Вправо
                elif ch_lower in ('q', 'й'): dx, dy = -self.current_step_mm, -self.current_step_mm
                elif ch_lower in ('e', 'у'): dx, dy = self.current_step_mm, -self.current_step_mm
                elif ch_lower in ('z', 'я'): dx, dy = -self.current_step_mm, self.current_step_mm
                elif ch_lower in ('c', 'с'): dx, dy = self.current_step_mm, self.current_step_mm
                elif ch == ' ':
                    # Переключение магнита
                    cur = getattr(self.controller, "magnet_active", False)
                    self.controller.set_electromagnet(not cur)
                elif ch_lower in ('p', 'з'):
                    # Переключение питания
                    self.toggle_power()

                if dx != 0.0 or dy != 0.0:
                    self.controller.move_cartesian(dx, dy, self.speed_mm_s)

        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            print(f"\n{C_CYAN}Интерактивный режим завершен.{C_RESET}\n")

    def print_menu(self):
        """Главное меню CLI."""
        print(f"\n{C_BOLD}{C_CYAN}============================================================={C_RESET}")
        print(f"{C_BOLD}           TMC2209 STEPPER DRIVERS TEST MENU{C_RESET}")
        print(f"  Режим: {'ЭМУЛЯЦИЯ (--dry-run)' if self.dry_run else 'АППАРАТНЫЙ (pigpio DMA)'}")
        print(f"{C_BOLD}{C_CYAN}============================================================={C_RESET}")
        print(f"  {C_GREEN}1.{C_RESET} Тест ЛЕВОГО мотора (Вперед / Назад 20 мм)")
        print(f"  {C_GREEN}2.{C_RESET} Тест ПРАВОГО мотора (Вперед / Назад 20 мм)")
        print(f"  {C_GREEN}3.{C_RESET} {C_BOLD}Интерактивное управление CoreXY (WASD + диагонали){C_RESET}")
        print(f"  {C_GREEN}4.{C_RESET} Мониторинг пинов StallGuard4 DIAG в реальном времени")
        print(f"  {C_GREEN}5.{C_RESET} Тест автопоиска нуля (Sensorless Homing)")
        print(f"  {C_GREEN}6.{C_RESET} Настройка TMC2209 по UART (Ток, StealthChop, SGTHRS)")
        print(f"  {C_GREEN}7.{C_RESET} Включение/выключение питания моторов (Enable)")
        print(f"  {C_GREEN}8.{C_RESET} Тест электромагнита (IRLZ44N)")
        print(f"  {C_GREEN}9.{C_RESET} Авто-тест квадрата (50х50 мм)")
        print(f"  {C_YELLOW}0.{C_RESET} Выход")
        print(f"{C_BOLD}{C_CYAN}============================================================={C_RESET}")

    def run_menu(self):
        """Запуск главного цикла меню."""
        while True:
            self.print_menu()
            choice = input("\nВыберите пункт меню [0-9]: ").strip()

            if choice == "1":
                self.test_single_motor("left")
            elif choice == "2":
                self.test_single_motor("right")
            elif choice == "3":
                self.interactive_jog()
            elif choice == "4":
                self.monitor_diag_pins()
            elif choice == "5":
                self.test_homing()
            elif choice == "6":
                self.configure_uart_interactive()
            elif choice == "7":
                self.toggle_power()
            elif choice == "8":
                self.test_magnet()
            elif choice == "9":
                self.test_square_auto()
            elif choice in ("0", "q", "exit"):
                print("Выход из тестирования. Обесточивание моторов...")
                if self.controller:
                    self.controller.cleanup()
                break
            else:
                print(f"{C_YELLOW}Неверный пункт меню!{C_RESET}")


def main():
    parser = argparse.ArgumentParser(description="Комплексный CLI тест драйверов шаговых двигателей TMC2209")
    parser.add_argument("--dry-run", action="store_true", help="Режим эмуляции без взаимодействия с реальным GPIO")
    parser.add_argument("--jog", action="store_true", help="Сразу запустить интерактивный режим управления WASD")
    parser.add_argument("--diag", action="store_true", help="Сразу запустить живой мониторинг пинов DIAG")
    parser.add_argument("--homing", action="store_true", help="Сразу запустить автопоиск нуля StallGuard")
    parser.add_argument("--auto", action="store_true", help="Запустить автоматический тест квадрата 50х50 мм")
    args = parser.parse_args()

    tester = TMC2209CLITester(dry_run=args.dry_run)

    if args.jog:
        tester.interactive_jog()
    elif args.diag:
        tester.monitor_diag_pins()
    elif args.homing:
        tester.test_homing()
    elif args.auto:
        tester.test_square_auto()
    else:
        tester.run_menu()


if __name__ == "__main__":
    main()
