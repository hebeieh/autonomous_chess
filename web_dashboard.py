#!/usr/bin/env python3
"""
Веб-интерфейс реального времени для тестирования матрицы 64 герконов (MCP23017)
и драйверов шаговых двигателей TMC2209.

Запуск:
    python3 web_dashboard.py                      # Аппаратный режим на Raspberry Pi
    python3 web_dashboard.py --mock-mcp --dry-run # Режим эмуляции на ПК (без оборудования)
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from typing import Dict, List, Optional, Set

from flask import Flask, Response, jsonify, render_template, request

import config
from src.corexy_wave import BaseWaveController, MockWaveController, create_wave_controller
from src.mcp23017_matrix import MCP23017ReedMatrix, CHESS_FILES, CHESS_RANKS
from src.tmc2209_driver import TMC2209UART, SensorlessHomingHelper


# Отключаем лишний шум логов Werkzeug
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__, template_folder="templates", static_folder="static")

# Глобальное состояние
class DashboardState:
    def __init__(self):
        self.matrix: Optional[MCP23017ReedMatrix] = None
        self.wave_controller: Optional[BaseWaveController] = None
        self.homing_helper: Optional[SensorlessHomingHelper] = None
        
        self.mock_mcp: bool = False
        self.dry_run: bool = False
        
        self.lock = threading.Lock()
        self.active_squares: List[str] = []
        self.active_set: Set[str] = set()
        self.last_update_ts: float = time.time()
        self.scan_hz: float = 0.0
        self.motors_enabled: bool = False
        self.magnet_active: bool = False
        
        # Позиция
        self.x_mm: float = 0.0
        self.y_mm: float = 0.0
        
        # Слушатели SSE
        self.subscribers = []
        self.running = True


state = DashboardState()


def background_matrix_scanner(poll_interval: float = 0.05):
    """Фоновый поток непрерывного сканирования матрицы герконов."""
    count_cycles = 0
    start_time = time.time()
    
    while state.running:
        t0 = time.time()
        try:
            if state.matrix:
                raw_active = state.matrix.get_active_squares()
                with state.lock:
                    new_set = set(raw_active)
                    if new_set != state.active_set:
                        state.active_set = new_set
                        state.active_squares = sorted(list(new_set))
                        state.last_update_ts = time.time()
                    
                    count_cycles += 1
                    elapsed = time.time() - start_time
                    if elapsed >= 1.0:
                        state.scan_hz = count_cycles / elapsed
                        count_cycles = 0
                        start_time = time.time()
        except Exception as e:
            print(f"[Scanner] Ошибка опроса матрицы: {e}")
            time.sleep(0.5)

        spent = time.time() - t0
        time.sleep(max(0.01, poll_interval - spent))


@app.route("/")
def index():
    """Главная страница дашборда."""
    return render_template(
        "index.html",
        files=CHESS_FILES,
        ranks=list(reversed(CHESS_RANKS)),
        mock_mcp=state.mock_mcp,
        dry_run=state.dry_run
    )


@app.route("/api/status")
def api_status():
    """Текущий статус оборудования в формате JSON."""
    with state.lock:
        return jsonify({
            "status": "ok",
            "active_squares": state.active_squares,
            "active_count": len(state.active_squares),
            "scan_hz": round(state.scan_hz, 1),
            "last_update": round(time.time() - state.last_update_ts, 1),
            "mock_mcp": state.mock_mcp,
            "dry_run": state.dry_run,
            "motors_enabled": state.motors_enabled,
            "magnet_active": state.magnet_active,
            "position": {
                "x_mm": round(state.x_mm, 2),
                "y_mm": round(state.y_mm, 2)
            }
        })


@app.route("/api/stream")
def api_stream():
    """Server-Sent Events (SSE) поток состояния матрицы и контроллера."""
    def event_stream():
        last_sent_squares = None
        last_sent_x = None
        last_sent_y = None
        last_sent_magnet = None
        last_sent_power = None
        last_heartbeat = time.time()
        
        while state.running:
            now = time.time()
            with state.lock:
                current_squares = list(state.active_squares)
                cur_x = round(state.x_mm, 2)
                cur_y = round(state.y_mm, 2)
                cur_magnet = state.magnet_active
                cur_power = state.motors_enabled
                scan_hz = round(state.scan_hz, 1)

            changed = (
                current_squares != last_sent_squares or
                cur_x != last_sent_x or
                cur_y != last_sent_y or
                cur_magnet != last_sent_magnet or
                cur_power != last_sent_power or
                (now - last_heartbeat > 0.5)
            )

            if changed:
                payload = {
                    "active_squares": current_squares,
                    "active_count": len(current_squares),
                    "scan_hz": scan_hz,
                    "motors_enabled": cur_power,
                    "magnet_active": cur_magnet,
                    "position": {"x": cur_x, "y": cur_y},
                    "timestamp": now
                }
                yield f"data: {json.dumps(payload)}\n\n"
                last_sent_squares = current_squares
                last_sent_x = cur_x
                last_sent_y = cur_y
                last_sent_magnet = cur_magnet
                last_sent_power = cur_power
                last_heartbeat = now

            time.sleep(0.04)  # ~25 Гц частота обновления для плавного отклика UI

    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


@app.route("/api/motor/jog", methods=["POST"])
def api_jog():
    """Шаговое перемещение каретки."""
    data = request.get_json(silent=True) or {}
    dx = float(data.get("dx", 0.0))
    dy = float(data.get("dy", 0.0))
    default_spd = getattr(config, "DEFAULT_TEST_SPEED_MM_S", 25.0)
    speed = float(data.get("speed", default_spd))

    with state.lock:
        new_x = state.x_mm + dx
        new_y = state.y_mm + dy

        # Проверка программных концевиков
        if not (0.0 <= new_x <= config.MAX_X_MM and 0.0 <= new_y <= config.MAX_Y_MM):
            return jsonify({
                "status": "error",
                "message": f"Выход за габариты! Цель: X={new_x:.1f}, Y={new_y:.1f}. Пределы: [0..{config.MAX_X_MM}, 0..{config.MAX_Y_MM}]"
            }), 400

        try:
            if state.wave_controller:
                if not state.motors_enabled:
                    state.wave_controller.enable_motors()
                    state.motors_enabled = True

                state.wave_controller.move_cartesian(dx, dy, speed)
                state.x_mm = new_x
                state.y_mm = new_y
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "ok",
        "position": {"x": round(state.x_mm, 2), "y": round(state.y_mm, 2)}
    })


@app.route("/api/motor/homing", methods=["POST"])
def api_homing():
    """Запуск поиска нуля (StallGuard Homing или сброс в эмуляторе)."""
    with state.lock:
        try:
            if state.homing_helper and not state.dry_run:
                # Аппаратный StallGuard Homing
                res_x = state.homing_helper.home_axis("X")
                res_y = state.homing_helper.home_axis("Y")
                state.x_mm = 0.0
                state.y_mm = 0.0
                return jsonify({
                    "status": "ok",
                    "message": f"StallGuard Homing завершен: X={res_x}, Y={res_y}",
                    "position": {"x": 0.0, "y": 0.0}
                })
            else:
                # Эмуляция Homing
                time.sleep(0.3)
                state.x_mm = 0.0
                state.y_mm = 0.0
                return jsonify({
                    "status": "ok",
                    "message": "Калибровка нуля выполнена (эмуляция): X=0.0, Y=0.0",
                    "position": {"x": 0.0, "y": 0.0}
                })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/motor/power", methods=["POST"])
def api_motor_power():
    """Включение/выключение питания обмоток шаговых двигателей."""
    data = request.get_json(silent=True) or {}
    with state.lock:
        target_state = data.get("state")
        if target_state is None:
            # Переключение тумблера
            target_state = not state.motors_enabled

        try:
            if state.wave_controller:
                if target_state:
                    state.wave_controller.enable_motors()
                else:
                    state.wave_controller.disable_motors()
            state.motors_enabled = bool(target_state)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "ok",
        "motors_enabled": state.motors_enabled
    })


@app.route("/api/magnet", methods=["POST"])
def api_magnet():
    """Включение/выключение электромагнита захвата фигур."""
    data = request.get_json(silent=True) or {}
    with state.lock:
        target_state = data.get("state")
        if target_state is None:
            target_state = not state.magnet_active

        try:
            if state.wave_controller:
                state.wave_controller.set_electromagnet(bool(target_state))
            state.magnet_active = bool(target_state)
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({
        "status": "ok",
        "magnet_active": state.magnet_active
    })


@app.route("/api/mock/toggle", methods=["POST"])
def api_mock_toggle():
    """Переключение состояния клетки в режиме эмуляции (по клику на доску)."""
    if not state.mock_mcp:
        return jsonify({"status": "error", "message": "Работает только в режиме --mock-mcp"}), 400

    data = request.get_json(silent=True) or {}
    sq = str(data.get("square", "")).lower().strip()
    if not sq:
        return jsonify({"status": "error", "message": "Не указана клетка (square)"}), 400

    with state.lock:
        is_active = sq in state.active_set
        new_active = not is_active
        if state.matrix:
            state.matrix.set_mock_square(sq, new_active)
        if new_active:
            state.active_set.add(sq)
        else:
            state.active_set.discard(sq)
        state.active_squares = sorted(list(state.active_set))
        state.last_update_ts = time.time()

    return jsonify({
        "status": "ok",
        "square": sq,
        "active": new_active,
        "active_squares": state.active_squares
    })


@app.route("/api/mock/reset", methods=["POST"])
def api_mock_reset():
    """Сброс/расстановка стартовой позиции в режиме эмуляции."""
    if not state.mock_mcp:
        return jsonify({"status": "error", "message": "Работает только в режиме --mock-mcp"}), 400

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "clear")  # "clear" или "standard"

    with state.lock:
        if state.matrix:
            state.matrix._mock_active_squares.clear()
            if mode == "standard":
                # Стандартная расстановка 32 фигур (ряды 1, 2, 7, 8)
                for r in ["1", "2", "7", "8"]:
                    for f in CHESS_FILES:
                        state.matrix.set_mock_square(f"{f}{r}", True)

        state.active_squares = state.matrix.get_active_squares() if state.matrix else []
        state.active_set = set(state.active_squares)
        state.last_update_ts = time.time()

    return jsonify({
        "status": "ok",
        "mode": mode,
        "active_count": len(state.active_squares),
        "active_squares": state.active_squares
    })


def create_app(mock_mcp: bool = False, dry_run: bool = False):
    """Инициализация приложения и оборудования."""
    state.mock_mcp = mock_mcp
    state.dry_run = dry_run

    # Инициализация матрицы MCP23017
    try:
        state.matrix = MCP23017ReedMatrix(
            bus_num=getattr(config, "MCP23017_I2C_BUS", 1),
            address=getattr(config, "MCP23017_I2C_ADDRESS", 0x20),
            mock=mock_mcp
        )
    except Exception as e:
        print(f"[Dashboard] Ошибка связи с MCP23017: {e}")
        print("[Dashboard] АВТОМАТИЧЕСКИЙ переход в режим эмуляции матрицы (--mock-mcp)!")
        state.mock_mcp = True
        state.matrix = MCP23017ReedMatrix(mock=True)

    # В режиме mock добавим начальные фигуры e2, d2 для наглядности
    if state.mock_mcp:
        state.matrix.set_mock_square("e2", True)
        state.matrix.set_mock_square("d2", True)
        state.active_squares = state.matrix.get_active_squares()
        state.active_set = set(state.active_squares)

    # Инициализация контроллера CoreXY
    state.wave_controller = create_wave_controller(dry_run=dry_run)

    # Инициализация помощника StallGuard Homing
    if hasattr(state.wave_controller, "pi") and state.wave_controller.pi is not None:
        state.homing_helper = SensorlessHomingHelper(state.wave_controller.pi, dry_run=dry_run)
    else:
        state.homing_helper = SensorlessHomingHelper(None, dry_run=True)

    # Запуск фонового потока опроса матрицы
    scanner_thread = threading.Thread(target=background_matrix_scanner, daemon=True)
    scanner_thread.start()

    return app


def main():
    parser = argparse.ArgumentParser(description="Веб-дашборд тестирования герконовой матрицы и приводов TMC2209")
    parser.add_argument("--mock-mcp", action="store_true", help="Эмуляция матрицы MCP23017 без физического чипа I2C")
    parser.add_argument("--dry-run", action="store_true", help="Эмуляция моторов без физического подключения pigpio")
    parser.add_argument("--host", default="0.0.0.0", help="Хост для запуска (по умолчанию 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5000, help="Порт веб-сервера (по умолчанию 5000)")
    args = parser.parse_args()

    app_instance = create_app(mock_mcp=args.mock_mcp, dry_run=args.dry_run)

    print("\n" + "=" * 65)
    print("  АВТОНОМНЫЕ ШАХМАТЫ: ВЕБ-ПАНЕЛЬ ТЕСТИРОВАНИЯ МАТРИЦЫ И TMC2209")
    print("=" * 65)
    print(f"  Веб-интерфейс запущен на:  http://localhost:{args.port}")
    print(f"  В локальной сети:           http://0.0.0.0:{args.port} (или IP Raspberry Pi)")
    print(f"  Матрица MCP23017:           {'ЭМУЛЯЦИЯ' if state.mock_mcp else 'АППАРАТНАЯ (I2C-1, 0x20)'}")
    print(f"  Драйверы TMC2209:           {'ЭМУЛЯЦИЯ' if state.dry_run else 'АППАРАТНЫЕ (pigpio DMA)'}")
    print("=" * 65 + "\n")

    try:
        app_instance.run(host=args.host, port=args.port, threaded=True)
    except KeyboardInterrupt:
        print("\nОстановка сервера...")
    finally:
        state.running = False
        if state.matrix:
            state.matrix.close()
        if state.wave_controller:
            state.wave_controller.cleanup()


if __name__ == "__main__":
    main()
