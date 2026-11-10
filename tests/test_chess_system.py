"""
Тестовый набор для проверки геометрических координат, манипулятора
и шахматной логики (включая взятия и кладбища).
"""

import unittest
import chess
import config
from src.coordinates import square_to_xy, get_graveyard_xy, cell_to_xy
from src.corexy_wave import MockWaveController
from src.piece_mover import PieceMover
from src.chess_game import ChessGameSimulation


class TestChessCoordinates(unittest.TestCase):
    def test_board_corners(self):
        # a8 (верхний левый угол доски): col 2, row 0
        x, y = square_to_xy('a8')
        self.assertAlmostEqual(x, config.OFFSET_X_MM + 2.5 * config.SQUARE_SIZE_MM)
        self.assertAlmostEqual(y, config.OFFSET_Y_MM + 0.5 * config.SQUARE_SIZE_MM)
        self.assertTrue(0 <= x <= config.WORK_AREA_X_MM)
        self.assertTrue(0 <= y <= config.WORK_AREA_Y_MM)

        # h1 (нижний правый угол доски): col 9, row 7
        x, y = square_to_xy('h1')
        self.assertAlmostEqual(x, config.OFFSET_X_MM + 9.5 * config.SQUARE_SIZE_MM)
        self.assertAlmostEqual(y, config.OFFSET_Y_MM + 7.5 * config.SQUARE_SIZE_MM)
        self.assertTrue(0 <= x <= config.WORK_AREA_X_MM)
        self.assertTrue(0 <= y <= config.WORK_AREA_Y_MM)

    def test_graveyard_bounds(self):
        # Проверяем все 16 слотов белого кладбища (слева)
        for i in range(16):
            x, y = get_graveyard_xy(piece_color_is_white=True, slot_index=i)
            self.assertTrue(0 <= x <= config.OFFSET_X_MM + 2 * config.SQUARE_SIZE_MM)
            self.assertTrue(0 <= y <= config.WORK_AREA_Y_MM)

        # Проверяем все 16 слотов черного кладбища (справа)
        for i in range(16):
            x, y = get_graveyard_xy(piece_color_is_white=False, slot_index=i)
            self.assertTrue(config.OFFSET_X_MM + 10 * config.SQUARE_SIZE_MM <= x <= config.WORK_AREA_X_MM)
            self.assertTrue(0 <= y <= config.WORK_AREA_Y_MM)


class TestChessPieceMoverAndGame(unittest.TestCase):
    def test_capture_and_graveyard(self):
        controller = MockWaveController()
        mover = PieceMover(controller)
        game = ChessGameSimulation(mover=mover, use_stockfish=False)

        # Сыграем ходы до взятия:
        # 1. e4 d5 2. exd5
        moves = [
            chess.Move.from_uci("e2e4"),
            chess.Move.from_uci("d7d5"),
            chess.Move.from_uci("e4d5")  # Взятие пешки d5 белой пешкой
        ]

        for m in moves:
            game.execute_physical_move(m)

        # Черная пешка с d5 должна быть отправлена на правое кладбище (black_graveyard_count = 1)
        self.assertEqual(mover.black_graveyard_count, 1)
        self.assertEqual(mover.white_graveyard_count, 0)
        self.assertEqual(game.board.piece_at(chess.D5).color, chess.WHITE)

        mover.return_to_origin()
        self.assertAlmostEqual(controller.current_x, 0.0, places=1)
        self.assertAlmostEqual(controller.current_y, 0.0, places=1)

    def test_knight_seam_routing(self):
        controller = MockWaveController()
        mover = PieceMover(controller)
        game = ChessGameSimulation(mover=mover, use_stockfish=False)

        # 1. Ход конем: b1 -> c3
        move_nc3 = chess.Move.from_uci("b1c3")
        game.execute_physical_move(move_nc3)

        # Проверяем, что конечная позиция каретки совпадает с центром c3
        target_x, target_y = square_to_xy("c3")
        self.assertAlmostEqual(controller.current_x, target_x, places=1)
        self.assertAlmostEqual(controller.current_y, target_y, places=1)

        # 2. Ход черным конем: b8 -> c6
        move_nc6 = chess.Move.from_uci("b8c6")
        game.execute_physical_move(move_nc6)

        target_nc6_x, target_nc6_y = square_to_xy("c6")
        self.assertAlmostEqual(controller.current_x, target_nc6_x, places=1)
        self.assertAlmostEqual(controller.current_y, target_nc6_y, places=1)

        mover.return_to_origin()
        self.assertAlmostEqual(controller.current_x, 0.0, places=1)
        self.assertAlmostEqual(controller.current_y, 0.0, places=1)


if __name__ == '__main__':
    unittest.main()
