"""
Высокоуровневый физический манипулятор шахматными фигурами.

Управляет захватом (электромагнит), перемещением по доске со сниженной скоростью
и эвакуацией сбитых фигур в соответствующие зоны кладбища.
"""

from typing import Tuple
import config
from src.coordinates import square_to_xy, get_graveyard_xy
from src.corexy_wave import BaseWaveController


class PieceMover:
    """Манипулятор фигурами на шахматной доске CoreXY."""

    def __init__(
        self,
        controller: BaseWaveController,
        speed_empty_mm_s: float = 40.0,
        speed_piece_mm_s: float = 20.0
    ):
        self.controller = controller
        self.speed_empty = speed_empty_mm_s
        self.speed_piece = speed_piece_mm_s

        # Счетчики занятых слотов на кладбищах (от 0 до 15)
        self.white_graveyard_count = 0
        self.black_graveyard_count = 0

    def move_to_coords(self, target_x: float, target_y: float, speed: float):
        """Переместить каретку в абсолютные координаты (X, Y) в мм."""
        dx = target_x - self.controller.current_x
        dy = target_y - self.controller.current_y

        if abs(dx) < 0.1 and abs(dy) < 0.1:
            return  # Уже в нужной точке

        self.controller.move_cartesian(dx, dy, speed)

    def move_to_square(self, square_str: str, speed: float):
        """Переместить каретку в центр указанной клетки (например, 'e4')."""
        tx, ty = square_to_xy(square_str)
        self.move_to_coords(tx, ty, speed)

    def pickup_piece(self, square_str: str):
        """
        Переместиться к фигуре с выключенным магнитом (на скорости холостого хода)
        и включить электромагнит для захвата.
        """
        self.move_to_square(square_str, self.speed_empty)
        self.controller.set_electromagnet(True)

    def drop_piece(self):
        """Отпустить фигуру, отключив электромагнит."""
        self.controller.set_electromagnet(False)

    def move_knight_via_seams(self, from_sq: str, to_sq: str):
        """
        Перемещение коня по межклеточным швам (Seam Routing) для предотвращения
        столкновения со стоящими рядом фигурами.

        Траектория разбивается на 3 прямолинейных отрезка:
        1. Смещение на 17 мм (половина клетки) из центра начальной клетки на шов.
        2. Движение строго по межклеточному шву на 2 клетки мимо стоящих фигур.
        3. Смещение на 17 мм со шва в центр целевой клетки.
        """
        print(f"[MANIPULATOR] Перенос коня по швам: {from_sq} -> {to_sq}")
        self.pickup_piece(from_sq)

        x1, y1 = square_to_xy(from_sq)
        x2, y2 = square_to_xy(to_sq)

        dx = x2 - x1
        dy = y2 - y1

        half_sq = config.SQUARE_SIZE_MM / 2.0  # 17.0 мм
        sign_x = 1.0 if dx > 0 else (-1.0 if dx < 0 else 0.0)
        sign_y = 1.0 if dy > 0 else (-1.0 if dy < 0 else 0.0)

        # Анализируем длинную и короткую сторону хода коня
        if abs(dx) < abs(dy):
            # Ход на 1 вертикаль и 2 горизонтали (например, b1 -> c3)
            # Точка 1: выход на вертикальный шов между начальной и конечной вертикалью
            w1_x = x1 + sign_x * half_sq
            w1_y = y1

            # Точка 2: проезд по вертикальному шву до уровня целевой горизонтали
            w2_x = w1_x
            w2_y = y2
        else:
            # Ход на 2 вертикали и 1 горизонталь (например, c3 -> e4)
            # Точка 1: выход на горизонтальный шов между начальной и конечной горизонталью
            w1_x = x1
            w1_y = y1 + sign_y * half_sq

            # Точка 2: проезд по горизонтальному шву до уровня целевой вертикали
            w2_x = x2
            w2_y = w1_y

        # Выполняем 3 отрезка перемещения
        self.move_to_coords(w1_x, w1_y, self.speed_piece)
        self.move_to_coords(w2_x, w2_y, self.speed_piece)
        self.move_to_coords(x2, y2, self.speed_piece)

        self.drop_piece()

    def transfer_piece(self, from_sq: str, to_sq: str, is_knight: bool = False):
        """
        Полный цикл перемещения фигуры:
        1. Холостой ход к исходной клетке.
        2. Захват электромагнитом.
        3. Транспортировка к целевой клетке (по швам для коня, прямо для остальных).
        4. Отпускание фигуры.
        """
        if is_knight:
            self.move_knight_via_seams(from_sq, to_sq)
            return

        print(f"[MANIPULATOR] Перенос фигуры: {from_sq} -> {to_sq}")
        self.pickup_piece(from_sq)
        target_x, target_y = square_to_xy(to_sq)
        self.move_to_coords(target_x, target_y, self.speed_piece)
        self.drop_piece()

    def capture_piece_to_graveyard(self, square_str: str, is_white_piece: bool) -> Tuple[float, float]:
        """
        Эвакуация сбитой фигуры с поля на соответствующее кладбище:
        - Белые сбитые фигуры -> Левое кладбище (колонки 0-1)
        - Черные сбитые фигуры -> Правое кладбище (колонки 10-11)
        """
        if is_white_piece:
            slot = min(self.white_graveyard_count, 15)
            self.white_graveyard_count += 1
            side_str = "Белая фигура -> Левое кладбище"
        else:
            slot = min(self.black_graveyard_count, 15)
            self.black_graveyard_count += 1
            side_str = "Черная фигура -> Правое кладбище"

        gx, gy = get_graveyard_xy(is_white_piece, slot)
        print(f"[MANIPULATOR] Взятие: {square_str} ({side_str}, слот #{slot})")

        self.pickup_piece(square_str)
        self.move_to_coords(gx, gy, self.speed_piece)
        self.drop_piece()
        return gx, gy

    def return_to_origin(self):
        """Возврат каретки в исходную точку (0, 0) на холостой скорости."""
        print("[MANIPULATOR] Возврат каретки в исходную точку (0, 0)...")
        self.controller.set_electromagnet(False)
        self.move_to_coords(0.0, 0.0, self.speed_empty)
