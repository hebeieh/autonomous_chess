#!/usr/bin/env python3
"""
Главная точка входа для симуляции игры двух ботов на автономной шахматной доске.

Пример запуска:
  # Запуск в режиме эмуляции на 6 ходов:
  python3 src/main.py --dry-run --max-moves 6

  # Запуск на Raspberry Pi со Stockfish:
  python3 src/main.py --max-moves 10
"""

import argparse
import sys
import time

# Добавляем корневую директорию проекта в sys.path
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
from src.corexy_wave import create_wave_controller
from src.piece_mover import PieceMover
from src.chess_game import ChessGameSimulation


def main():
    parser = argparse.ArgumentParser(
        description="Симуляция игры двух шахматных ботов на механике CoreXY с электромагнитом"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Режим эмуляции без использования реального GPIO'
    )
    parser.add_argument(
        '--max-moves',
        type=int,
        default=6,
        help='Максимальное количество полуходов для симуляции (по умолчанию 6, 0 = играть до конца)'
    )
    parser.add_argument(
        '--stockfish-path',
        type=str,
        default="/usr/games/stockfish",
        help='Путь к исполняемому файлу Stockfish'
    )
    parser.add_argument(
        '--no-stockfish',
        action='store_true',
        help='Использовать только встроенный эвристический бот без Stockfish'
    )
    parser.add_argument(
        '--time-limit',
        type=float,
        default=0.5,
        help='Лимит времени на расчет одного хода ботом в секундах (по умолчанию 0.5)'
    )
    parser.add_argument(
        '--speed-empty',
        type=float,
        default=40.0,
        help='Скорость холостого хода каретки в мм/с (по умолчанию 40.0)'
    )
    parser.add_argument(
        '--speed-piece',
        type=float,
        default=20.0,
        help='Скорость перемещения с включенным магнитом в мм/с (по умолчанию 20.0)'
    )

    args = parser.parse_args()

    print("=" * 70)
    print("   СИМУЛЯЦИЯ ШАХМАТНОЙ ПАРТИИ БОТОВ НА АВТОНОМНОЙ ДОСКЕ COREXY")
    print("=" * 70)
    print(f"Параметры:")
    print(f" - Режим:             {'[ЭМУЛЯЦИЯ / DRY-RUN]' if args.dry_run else '[РЕАЛЬНОЕ ЖЕЛЕЗО / PIGPIO WAVES]'}")
    print(f" - Скорость холостая: {args.speed_empty:.1f} мм/с")
    print(f" - Скорость буксира:  {args.speed_piece:.1f} мм/с")
    print(f" - Разрешение шага:   {config.STEPS_PER_MM} шагов/мм (микрошаг 1/16, шкив 20T GT2)")
    print(f" - Макс. ходов:       {args.max_moves if args.max_moves > 0 else 'Без ограничений'}")
    print("=" * 70)
    print("ВАЖНО: Перед стартом убедитесь, что каретка вручную выставлена в (0,0)")
    print("(верхний левый угол) и поле свободно для движения!")
    print("=" * 70 + "\n")

    # Инициализация аппаратного контроллера волн
    controller = create_wave_controller(dry_run=args.dry_run)
    mover = PieceMover(
        controller=controller,
        speed_empty_mm_s=args.speed_empty,
        speed_piece_mm_s=args.speed_piece
    )
    game = ChessGameSimulation(
        mover=mover,
        stockfish_path=args.stockfish_path,
        time_limit=args.time_limit,
        use_stockfish=not args.no_stockfish
    )

    try:
        # Включение драйверов моторов
        controller.enable_motors()
        time.sleep(0.3)

        print("[GAME] Начало партии. Начальная расстановка фигур:")
        game.print_board()

        move_count = 0
        while not game.board.is_game_over():
            if args.max_moves > 0 and move_count >= args.max_moves:
                print(f"\n[GAME] Достигнут лимит полуходов ({args.max_moves}). Завершение партии.")
                break

            move = game.get_bot_move()
            if move is None:
                print("[GAME] Нет доступных ходов.")
                break

            game.execute_physical_move(move)
            move_count += 1
            game.print_board()

            # Короткая пауза между ходами для визуального восприятия
            time.sleep(0.5)

        # Вывод результата игры
        if game.board.is_checkmate():
            winner = "Черные" if game.board.turn == chess.WHITE else "Белые"
            print(f"\n[GAME] МАТ! Победили: {winner}!")
        elif game.board.is_stalemate():
            print("\n[GAME] ПАТ! Ничья.")
        elif game.board.is_insufficient_material():
            print("\n[GAME] Ничья из-за недостатка материала.")
        elif game.board.can_claim_draw():
            print("\n[GAME] Ничья по правилу троекратного повторения или 50 ходов.")

        # Возврат каретки в исходную позицию (0,0)
        mover.return_to_origin()

    except KeyboardInterrupt:
        print("\n\nПрерывание пользователем (Ctrl+C). Экстренная остановка...")
    finally:
        print("\n[CLEANUP] Завершение работы, освобождение ресурсов...")
        game.close()
        controller.cleanup()
        print("[CLEANUP] Готово.")


if __name__ == '__main__':
    main()
