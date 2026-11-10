"""
Модуль трансляции шахматных нотаций и служебных позиций (кладбище)
в физические координаты (X, Y) в миллиметрах на рабочем поле.
"""

from typing import Tuple
import config

# Соответствие шахматных вертикалей (a-h) индексам колонок на сетке (2-9)
# Колонки 0 и 1 отведены под левое кладбище
# Колонки 10 и 11 отведены под правое кладбище
FILE_TO_COL = {
    'a': 2, 'b': 3, 'c': 4, 'd': 5,
    'e': 6, 'f': 7, 'g': 8, 'h': 9
}

COL_TO_FILE = {v: k for k, v in FILE_TO_COL.items()}


def cell_to_xy(col: int, row: int) -> Tuple[float, float]:
    """
    Преобразование индексов ячейки сетки (col: 0..11, row: 0..7)
    в физические координаты центра ячейки (X, Y) в мм.

    Начало координат (0, 0) — верхний левый угол.
    """
    if not (0 <= col < config.GRID_COLS):
        raise ValueError(f"Колонка {col} выходит за пределы сетки [0..{config.GRID_COLS - 1}]")
    if not (0 <= row < config.GRID_ROWS):
        raise ValueError(f"Ряд {row} выходит за пределы сетки [0..{config.GRID_ROWS - 1}]")

    x = config.OFFSET_X_MM + (col + 0.5) * config.SQUARE_SIZE_MM
    y = config.OFFSET_Y_MM + (row + 0.5) * config.SQUARE_SIZE_MM
    return round(x, 2), round(y, 2)


def square_to_xy(square_name: str) -> Tuple[float, float]:
    """
    Преобразование стандартной шахматной нотации (например, 'e4', 'a1', 'h8')
    в координаты центра клетки (X, Y) в мм.

    Ряд 8 находится сверху (row=0, близко к (0,0)).
    Ряд 1 находится снизу (row=7, близко к моторам).
    """
    square_name = square_name.lower().strip()
    if len(square_name) != 2:
        raise ValueError(f"Некорректное имя клетки: '{square_name}'")

    file_char, rank_char = square_name[0], square_name[1]

    if file_char not in FILE_TO_COL:
        raise ValueError(f"Неизвестная вертикаль '{file_char}' в клетке '{square_name}'")
    if not ('1' <= rank_char <= '8'):
        raise ValueError(f"Неизвестная горизонталь '{rank_char}' в клетке '{square_name}'")

    col = FILE_TO_COL[file_char]
    rank = int(rank_char)
    row = 8 - rank  # rank 8 -> row 0, rank 1 -> row 7

    return cell_to_xy(col, row)


def get_graveyard_xy(piece_color_is_white: bool, slot_index: int) -> Tuple[float, float]:
    """
    Возвращает физические координаты ячейки кладбища для сбитой фигуры.

    - Белые сбитые фигуры (piece_color_is_white=True) помещаются
      в левое кладбище: колонки 0 и 1 (всего 16 слотов, 0..15).
    - Черные сбитые фигуры (piece_color_is_white=False) помещаются
      в правое кладбище: колонки 10 и 11 (всего 16 слотов, 0..15).

    Заполнение происходит по рядам от верхнего (ряд 0) к нижнему (ряд 7):
    слоты 0..7 в первой колонке кладбища, 8..15 во второй.
    """
    if not (0 <= slot_index < 16):
        raise ValueError(f"Индекс слота кладбища должен быть в диапазоне 0..15, получено: {slot_index}")

    row = slot_index % 8
    sub_col = slot_index // 8  # 0 или 1

    if piece_color_is_white:
        # Левое кладбище: колонки 0 и 1
        col = sub_col
    else:
        # Правое кладбище: колонки 10 и 11
        col = 10 + sub_col

    return cell_to_xy(col, row)
