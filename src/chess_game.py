"""
Игровой движок автономной шахматной доски.
Интегрирует python-chess, движок Stockfish через протокол UCI
и координирует физические перемещения каретки с фигурами.
"""

import os
import random
import sys
import time
from typing import Optional, Tuple

import chess
import chess.engine

from src.piece_mover import PieceMover


# Базовая оценка стоимости фигур для встроенного эвристического бота
PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 20000
}


class BuiltinHeuristicBot:
    """
    Легковесный встроенный бот для ситуаций, когда Stockfish не установлен.
    Оценивает легальные ходы по материалу и контролю центра без тяжелых вычислений.
    """

    def __init__(self, color_name: str):
        self.color_name = color_name

    def select_move(self, board: chess.Board) -> chess.Move:
        legal_moves = list(board.legal_moves)
        if not legal_moves:
            return None

        best_score = -999999
        best_moves = []

        turn_factor = 1 if board.turn == chess.WHITE else -1

        for move in legal_moves:
            score = 0

            # Бонус за взятие
            if board.is_capture(move):
                captured = board.piece_at(move.to_square)
                if captured:
                    score += PIECE_VALUES.get(captured.piece_type, 100) * 10
                elif board.is_en_passant(move):
                    score += PIECE_VALUES[chess.PAWN] * 10

            # Бонус за продвижение к центру (d4, d5, e4, e5)
            to_file = chess.square_file(move.to_square)
            to_rank = chess.square_rank(move.to_square)
            dist_to_center = abs(to_file - 3.5) + abs(to_rank - 3.5)
            score -= dist_to_center * 5

            # Небольшой случайный фактор для разнообразия партий
            score += random.randint(-10, 10)

            if score > best_score:
                best_score = score
                best_moves = [move]
            elif score == best_score:
                best_moves.append(move)

        return random.choice(best_moves)


class StockfishBot:
    """Управление внешним движком Stockfish через протокол UCI."""

    def __init__(
        self,
        binary_path: str = "/usr/games/stockfish",
        time_limit: float = 0.5,
        skill_level: int = 3,
        threads: int = 1,
        hash_mb: int = 16
    ):
        self.binary_path = binary_path
        self.time_limit = time_limit
        self.skill_level = skill_level
        self.threads = threads
        self.hash_mb = hash_mb
        self.engine = None
        self._init_engine()

    def _init_engine(self):
        # Проверка стандартных путей к бинарнику Stockfish
        candidate_paths = [
            self.binary_path,
            "/usr/games/stockfish",
            "/usr/bin/stockfish",
            "/usr/local/bin/stockfish",
            "stockfish"
        ]

        found_path = None
        for p in candidate_paths:
            if os.path.exists(p) or self._check_in_path(p):
                found_path = p
                break

        if found_path is None:
            raise FileNotFoundError(f"Бинарный файл Stockfish не найден по путям: {candidate_paths}")

        print(f"[STOCKFISH] Запуск движка UCI: {found_path}")
        self.engine = chess.engine.SimpleEngine.popen_uci(found_path)

        # Конфигурация для экономии ресурсов Raspberry Pi Zero 2W
        self.engine.configure({
            "Threads": self.threads,
            "Hash": self.hash_mb,
            "Skill Level": self.skill_level
        })
        print(f"[STOCKFISH] Настройки: Threads={self.threads}, Hash={self.hash_mb}MB, Skill={self.skill_level}")

    def _check_in_path(self, name: str) -> bool:
        from shutil import which
        return which(name) is not None

    def select_move(self, board: chess.Board) -> Optional[chess.Move]:
        if board.is_game_over() or self.engine is None:
            return None

        result = self.engine.play(board, chess.engine.Limit(time=self.time_limit))
        return result.move

    def close(self):
        if self.engine:
            try:
                self.engine.quit()
            except Exception:
                pass
            self.engine = None


class ChessGameSimulation:
    """Оркестратор шахматной партии и физических движений."""

    def __init__(
        self,
        mover: PieceMover,
        stockfish_path: str = "/usr/games/stockfish",
        time_limit: float = 0.5,
        use_stockfish: bool = True
    ):
        self.mover = mover
        self.board = chess.Board()
        self.stockfish_bot = None

        if use_stockfish:
            try:
                self.stockfish_bot = StockfishBot(
                    binary_path=stockfish_path,
                    time_limit=time_limit
                )
            except Exception as e:
                print(f"[CHESS] ВНИМАНИЕ: Stockfish недоступен ({e}).")
                print("[CHESS] Переключение на встроенный эвристический бот (HeuristicBot).\n")
                self.stockfish_bot = None

        self.fallback_white = BuiltinHeuristicBot("Белые")
        self.fallback_black = BuiltinHeuristicBot("Черные")

    def get_bot_move(self) -> Optional[chess.Move]:
        """Получить следующий ход от текущего игрока."""
        if self.board.is_game_over():
            return None

        if self.stockfish_bot:
            try:
                return self.stockfish_bot.select_move(self.board)
            except Exception as e:
                print(f"[CHESS] Ошибка вызова Stockfish: {e}. Используем fallback бот.")

        bot = self.fallback_white if self.board.turn == chess.WHITE else self.fallback_black
        return bot.select_move(self.board)

    def execute_physical_move(self, move: chess.Move):
        """
        Выполнение физического перемещения фигуры кареткой с учетом взятия,
        рокировки и взятия на проходе.
        """
        from_sq = move.from_square
        to_sq = move.to_square

        from_str = chess.square_name(from_sq)
        to_str = chess.square_name(to_sq)

        turn_color_str = "Белые" if self.board.turn == chess.WHITE else "Черные"
        moving_piece = self.board.piece_at(from_sq)
        is_knight = (moving_piece is not None and moving_piece.piece_type == chess.KNIGHT)

        print(f"\n>>> Ход #{self.board.fullmove_number} ({turn_color_str}): {move.uci()} ({from_str} -> {to_str})")

        # 1. Проверка взятия
        if self.board.is_en_passant(move):
            # Взятие на проходе: сбиваемая пешка находится на соседней клетке
            if self.board.turn == chess.WHITE:
                captured_sq = chess.square(chess.square_file(to_sq), chess.square_rank(to_sq) - 1)
            else:
                captured_sq = chess.square(chess.square_file(to_sq), chess.square_rank(to_sq) + 1)

            captured_sq_str = chess.square_name(captured_sq)
            print(f"[CHESS] Взятие на проходе (en passant)! Пешка на {captured_sq_str} эвакуируется на кладбище")
            self.mover.capture_piece_to_graveyard(captured_sq_str, is_white_piece=(not self.board.turn))

        elif self.board.is_capture(move):
            captured_piece = self.board.piece_at(to_sq)
            if captured_piece:
                captured_is_white = (captured_piece.color == chess.WHITE)
                print(f"[CHESS] Взятие фигуры на {to_str} ({chess.piece_name(captured_piece.piece_type)})!")
                self.mover.capture_piece_to_graveyard(to_str, is_white_piece=captured_is_white)

        # 2. Проверка рокировки
        if self.board.is_castling(move):
            print(f"[CHESS] Рокировка ({move.uci()})!")
            # Сначала перемещаем короля
            self.mover.transfer_piece(from_str, to_str)

            # Затем перемещаем соответствующую ладью
            if to_str == 'g1':  # Белая короткая
                self.mover.transfer_piece('h1', 'f1')
            elif to_str == 'c1':  # Белая длинная
                self.mover.transfer_piece('a1', 'd1')
            elif to_str == 'g8':  # Черная короткая
                self.mover.transfer_piece('h8', 'f8')
            elif to_str == 'c8':  # Черная длинная
                self.mover.transfer_piece('a8', 'd8')

        else:
            # Обычный ход фигуры
            self.mover.transfer_piece(from_str, to_str, is_knight=is_knight)

        # 3. Превращение пешки
        if move.promotion:
            promoted_name = chess.piece_name(move.promotion)
            print(f"[CHESS] Превращение пешки в {promoted_name.upper()} на поле {to_str}!")

        # 4. Применение хода к логической доске
        self.board.push(move)

    def print_board(self):
        """Вывод текущего состояния шахматной доски в ASCII."""
        print("\n" + str(self.board) + "\n")

    def close(self):
        """Освобождение ресурсов движка."""
        if self.stockfish_bot:
            self.stockfish_bot.close()
