#!/usr/bin/env python3
"""
Консольная утилита для тестирования матрицы 64 герконов через MCP23017.
Отображает в терминале сетку 8х8 в реальном времени с цветовой подсветкой
клеток, где обнаружены магниты шахматных фигур.

Использование:
    python3 test_reed_cli.py               # Реальное оборудование на I2C-1 (0x20)
    python3 test_reed_cli.py --mock        # Режим эмуляции (без I2C)
    python3 test_reed_cli.py --once        # Однократное сканирование и выход
"""

import argparse
import os
import sys
import time

import config
from src.mcp23017_matrix import MCP23017ReedMatrix, CHESS_FILES, CHESS_RANKS


# ANSI цветовые коды
COLOR_RESET = "\033[0m"
COLOR_BOLD = "\033[1m"
COLOR_GREEN = "\033[92m"
COLOR_CYAN = "\033[96m"
COLOR_YELLOW = "\033[93m"
COLOR_DIM = "\033[90m"
COLOR_BG_GREEN = "\033[42m\033[30m"
COLOR_CLEAR_SCREEN = "\033[2J\033[H"


def render_board(active_squares: set, bus_info: str, poll_hz: float):
    """Отрисовка шахматной доски в терминале."""
    out = []
    out.append(f"{COLOR_BOLD}{COLOR_CYAN}================================================================{COLOR_RESET}")
    out.append(f"{COLOR_BOLD}  MCP23017 Reed Switch Matrix Real-Time Monitor (8x8 = 64){COLOR_RESET}")
    out.append(f"  {bus_info} | Частота: ~{poll_hz:.1f} Гц | Активных фигур: {COLOR_BOLD}{COLOR_GREEN}{len(active_squares)}{COLOR_RESET}")
    if active_squares:
        sorted_sq = sorted(list(active_squares))
        out.append(f"  {COLOR_YELLOW}Обнаружены магниты:{COLOR_RESET} {', '.join(sorted_sq)}")
    else:
        out.append(f"  {COLOR_DIM}Магниты не обнаружены (доска пуста){COLOR_RESET}")
    out.append(f"{COLOR_BOLD}{COLOR_CYAN}================================================================{COLOR_RESET}\n")

    # Верхний заголовок столбцов a-h
    header_files = "      " + "   ".join(CHESS_FILES)
    out.append(f"{COLOR_BOLD}{header_files}{COLOR_RESET}")
    out.append("    +" + "---+" * 8)

    # Строки от 8 вниз к 1
    for r in reversed(CHESS_RANKS):
        row_str = f"  {COLOR_BOLD}{r}{COLOR_RESET} |"
        for f in CHESS_FILES:
            sq = f"{f}{r}"
            if sq in active_squares:
                # Активная клетка с магнитом
                row_str += f"{COLOR_BG_GREEN} ● {COLOR_RESET}|"
            else:
                row_str += f"{COLOR_DIM} · {COLOR_RESET}|"
        row_str += f" {COLOR_BOLD}{r}{COLOR_RESET}"
        out.append(row_str)
        out.append("    +" + "---+" * 8)

    # Нижний заголовок столбцов a-h
    out.append(f"{COLOR_BOLD}{header_files}{COLOR_RESET}\n")
    out.append(f"{COLOR_DIM}Нажмите Ctrl+C для выхода{COLOR_RESET}")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="Тестирование матрицы 64 герконов MCP23017 в консоли")
    parser.add_argument("--mock", action="store_true", help="Запуск в режиме эмуляции без физического чипа MCP23017")
    parser.add_argument("--bus", type=int, default=getattr(config, "MCP23017_I2C_BUS", 1), help="Номер шины I2C (по умолчанию 1)")
    parser.add_argument("--address", type=lambda x: int(x, 0), default=getattr(config, "MCP23017_I2C_ADDRESS", 0x20),
                        help="I2C адрес MCP23017 (шестнадцатеричный, например 0x20)")
    parser.add_argument("--rate", type=float, default=15.0, help="Частота опроса матрицы в Гц (по умолчанию 15)")
    parser.add_argument("--once", action="store_true", help="Выполнить одно сканирование и вывести результат")
    args = parser.parse_args()

    delay = 1.0 / max(1.0, args.rate)
    bus_info = f"I2C-{args.bus} (0x{args.address:02X})" if not args.mock else "РЕЖИМ ЭМУЛЯЦИИ (--mock)"

    try:
        matrix = MCP23017ReedMatrix(
            bus_num=args.bus,
            address=args.address,
            mock=args.mock
        )
    except Exception as e:
        print(f"\n{COLOR_YELLOW}[ОШИБКА]{COLOR_RESET} Не удалось инициализировать MCP23017: {e}")
        print("Если вы тестируете на ПК без чипа MCP23017, добавьте флаг --mock:")
        print("    python3 test_reed_cli.py --mock\n")
        sys.exit(1)

    if args.mock:
        # Для демонстрации в режиме mock активируем классические начальные клетки (пешки e2, d2, фигуры d1, e1)
        matrix.set_mock_square("e2", True)
        matrix.set_mock_square("d2", True)
        matrix.set_mock_square("e1", True)
        matrix.set_mock_square("d1", True)

    try:
        if args.once:
            active = set(matrix.get_active_squares())
            print(render_board(active, bus_info, args.rate))
            return

        last_state = None
        count_frames = 0
        start_time = time.time()
        measured_rate = args.rate

        while True:
            t0 = time.time()
            active_list = matrix.get_active_squares()
            active_set = set(active_list)

            # Вычисление реальной частоты опроса
            count_frames += 1
            elapsed = time.time() - start_time
            if elapsed >= 1.0:
                measured_rate = count_frames / elapsed
                count_frames = 0
                start_time = time.time()

            # Обновляем экран терминала
            sys.stdout.write(COLOR_CLEAR_SCREEN)
            sys.stdout.write(render_board(active_set, bus_info, measured_rate))
            sys.stdout.flush()

            # Выдерживаем задержку цикла
            spent = time.time() - t0
            sleep_time = max(0.005, delay - spent)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\n{COLOR_CYAN}Остановлено пользователем.{COLOR_RESET}")
    finally:
        matrix.close()


if __name__ == "__main__":
    main()
