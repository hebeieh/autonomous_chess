"""
Тесты драйвера матрицы MCP23017, драйвера TMC2209 и веб-интерфейса Flask.
"""

import json
import pytest
from src.mcp23017_matrix import MCP23017ReedMatrix, CHESS_FILES, CHESS_RANKS
from src.tmc2209_driver import calc_crc, SensorlessHomingHelper, TMC2209Manager
from web_dashboard import create_app, state
import config


# ==============================================================================
# 1. Тесты MCP23017ReedMatrix
# ==============================================================================
def test_mcp23017_mock_initialization():
    matrix = MCP23017ReedMatrix(mock=True)
    assert matrix.mock is True
    # По умолчанию матрица пуста
    assert matrix.get_active_squares() == []
    matrix.close()


def test_mcp23017_mapping_and_toggle():
    matrix = MCP23017ReedMatrix(mock=True)
    matrix.set_mock_square("e4", True)
    matrix.set_mock_square("d5", True)

    active = matrix.get_active_squares()
    assert "e4" in active
    assert "d5" in active
    assert len(active) == 2

    # Проверка полного словаря 64 клеток
    full_scan = matrix.scan()
    assert len(full_scan) == 64
    assert full_scan["e4"] is True
    assert full_scan["d5"] is True
    assert full_scan["a1"] is False
    assert full_scan["h8"] is False

    # Снятие фигуры
    matrix.set_mock_square("e4", False)
    assert "e4" not in matrix.get_active_squares()
    assert len(matrix.get_active_squares()) == 1
    matrix.close()


def test_chess_files_and_ranks_structure():
    assert len(CHESS_FILES) == 8
    assert CHESS_FILES == ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
    assert len(CHESS_RANKS) == 8
    assert CHESS_RANKS == ['1', '2', '3', '4', '5', '6', '7', '8']


# ==============================================================================
# 2. Тесты TMC2209 (CRC8 и Homing)
# ==============================================================================
def test_tmc2209_crc_calculation():
    # Проверка CRC8 на базовых тестовых байтах
    # Полином TMC: x^8 + x^2 + x^1 + x^0
    data = bytes([0x05, 0x00, 0x80, 0x00, 0x00, 0x00, 0x00])
    crc = calc_crc(data)
    assert isinstance(crc, int)
    assert 0 <= crc <= 255


def test_tmc2209_homing_helper_dry_run():
    helper = SensorlessHomingHelper(pi=None, dry_run=True)
    res_x = helper.home_axis("X")
    assert res_x is True
    res_y = helper.home_axis("Y")
    assert res_y is True

    with pytest.raises(ValueError):
        helper.home_axis("Z")


# ==============================================================================
# 3. Тесты Flask Web Dashboard API
# ==============================================================================
@pytest.fixture
def client():
    app = create_app(mock_mcp=True, dry_run=True)
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_web_index_page(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.data.decode("utf-8")
    assert "Autonomous Chess Dashboard" in html
    assert 'data-square="e4"' in html
    assert 'data-square="a1"' in html
    assert 'data-square="h8"' in html


def test_web_status_endpoint(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "ok"
    assert "active_squares" in data
    assert "position" in data
    assert data["mock_mcp"] is True
    assert data["dry_run"] is True


def test_web_jog_endpoint(client):
    # Начальный сброс в (0, 0)
    client.post("/api/motor/homing")

    # Перемещение на +10 мм по X и +20 мм по Y
    response = client.post("/api/motor/jog", json={"dx": 10.0, "dy": 20.0, "speed": 40.0})
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data["status"] == "ok"
    assert data["position"]["x"] == 10.0
    assert data["position"]["y"] == 20.0

    # Проверка защиты от выхода за пределы (отрицательные координаты)
    resp_invalid = client.post("/api/motor/jog", json={"dx": -50.0, "dy": 0.0})
    assert resp_invalid.status_code == 400
    err_data = json.loads(resp_invalid.data)
    assert "Выход за габариты" in err_data["message"]


def test_web_magnet_endpoint(client):
    # Включение электромагнита
    resp1 = client.post("/api/magnet", json={"state": True})
    assert resp1.status_code == 200
    data1 = json.loads(resp1.data)
    assert data1["magnet_active"] is True

    # Переключение тумблера без указания state
    resp2 = client.post("/api/magnet", json={})
    assert resp2.status_code == 200
    data2 = json.loads(resp2.data)
    assert data2["magnet_active"] is False


def test_web_power_endpoint(client):
    resp = client.post("/api/motor/power", json={"state": True})
    assert resp.status_code == 200
    assert json.loads(resp.data)["motors_enabled"] is True

    resp_off = client.post("/api/motor/power", json={"state": False})
    assert resp_off.status_code == 200
    assert json.loads(resp_off.data)["motors_enabled"] is False


def test_web_mock_toggle_and_reset(client):
    # Сброс матрицы
    client.post("/api/mock/reset", json={"mode": "clear"})
    status = json.loads(client.get("/api/status").data)
    assert status["active_count"] == 0

    # Переключение клетки e4
    resp_toggle = client.post("/api/mock/toggle", json={"square": "e4"})
    assert resp_toggle.status_code == 200
    data_toggle = json.loads(resp_toggle.data)
    assert data_toggle["square"] == "e4"
    assert data_toggle["active"] is True
    assert "e4" in data_toggle["active_squares"]

    # Расстановка стандартной позиции (32 фигуры)
    resp_std = client.post("/api/mock/reset", json={"mode": "standard"})
    assert resp_std.status_code == 200
    data_std = json.loads(resp_std.data)
    assert data_std["active_count"] == 32
    assert "e1" in data_std["active_squares"]
    assert "e8" in data_std["active_squares"]
    assert "e4" not in data_std["active_squares"]


def test_web_homing_endpoint(client):
    resp = client.post("/api/motor/homing")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["status"] == "ok"
    assert data["position"]["x"] == 0.0
    assert data["position"]["y"] == 0.0
