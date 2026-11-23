"""
Файл конфигурации аппаратной части и параметров поля автономной шахматной доски.
Обновлен для драйверов TMC2209 (UART + StallGuard DIAG) и расширителя MCP23017 (I2C + INT).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class StepperDriverConfig:
    """Конфигурация шагового привода и драйвера TMC2209."""
    step_pin: int
    dir_pin: int
    enable_pin: int
    diag_pin: Optional[int] = None       # Пин DIAG (StallGuard4 sensorless homing)
    uart_pin: Optional[int] = None       # Пин программного UART (PDN_UART)
    uart_baudrate: int = 115200          # Скорость UART обмена
    current_ma: int = 800                # RMS рабочий ток двигателя в мА (NEMA 17 17HS4023)
    hold_current_percent: int = 50       # Ток удержания в % от рабочего
    stealthchop: bool = True             # Бесшумный режим StealthChop2 (True) или SpreadCycle (False)
    stallguard_threshold: int = 64       # Порог срабатывания StallGuard4 SGTHRS (0..255)
    invert_dir: bool = False


# Для обратной совместимости со старыми модулями
StepperPinConfig = StepperDriverConfig


# ==========================================
# 1. Распиновка GPIO (BCM нумерация выводов)
# ==========================================

# Левый шаговый мотор (Left / Motor A / X-диагональ)
# Расположен в нижнем левом углу стола
MOTOR_LEFT = StepperDriverConfig(
    step_pin=17,    # Pin 11: STEP Yellow
    dir_pin=27,     # Pin 13: DIR Blue
    enable_pin=22,  # Pin 15: ENABLE Green
    diag_pin=4,     # Pin 7:  DIAG (StallGuard4) Black
    uart_pin=14,    # Pin 8:  PDN_UART (Software UART) Red
    uart_baudrate=115200,
    current_ma=800,
    hold_current_percent=50,
    stealthchop=True,
    stallguard_threshold=64,
    invert_dir=False,
)

# Правый шаговый мотор (Right / Motor B / Y-диагональ)
# Расположен в нижнем правом углу стола
MOTOR_RIGHT = StepperDriverConfig(
    step_pin=23,    # Pin 16: STEP Yellow
    dir_pin=24,     # Pin 18: DIR Blue
    enable_pin=25,  # Pin 22: ENABLE Green
    diag_pin=5,     # Pin 29: DIAG (StallGuard4) Black
    uart_pin=15,    # Pin 10: PDN_UART (Software UART) Red
    uart_baudrate=115200,
    current_ma=800,
    hold_current_percent=50,
    stealthchop=True,
    stallguard_threshold=64,
    invert_dir=False,
)

# Управление затвором транзистора IRLZ44N для электромагнита
ELECTROMAGNET_PIN = 18  # Pin 12: PWM0 / GPIO

# Логика работы пина Enable для TMC2209 / DRV8825:
# 0 (LOW) = драйвер включен (мотор под током)
# 1 (HIGH) = драйвер выключен (обмотки обесточены)
TMC2209_ENABLE_ACTIVE_STATE = 0
DRV8825_ENABLE_ACTIVE_STATE = 0  # Алиас для обратной совместимости


# ==========================================
# 2. Модуль расширения портов MCP23017 (I2C)
#    для матрицы 64 герконов шахматного поля
# ==========================================

@dataclass
class MCP23017Config:
    """Конфигурация микросхемы MCP23017 на шине I2C."""
    i2c_bus: int = 1                     # Номер шины I2C (/dev/i2c-1)
    i2c_address: int = 0x20              # Базовый адрес I2C (A0=GND, A1=GND, A2=GND)
    sda_pin: int = 2                     # Pin 3:  I2C SDA
    scl_pin: int = 3                     # Pin 5:  I2C SCL
    interrupt_pin: Optional[int] = 6     # Pin 31: Прерывание INTA/B (Active LOW)
    matrix_rows: int = 8                 # Port A (GPA0..GPA7) — строки 8x8 матрицы (выходы)
    matrix_cols: int = 8                 # Port B (GPB0..GPB7) — столбцы 8x8 матрицы (входы с pull-up)
    pullup_enabled: bool = True          # Включение внутренних подтягивающих резисторов (GPPU)


MCP23017 = MCP23017Config()


# ==========================================
# 3. Кинематика CoreXY и механические параметры
# ==========================================

# Шаговые двигатели NEMA 17 (1.8° на шаг -> 200 шагов/оборот)
STEPS_PER_REV = 200

# Микрошаг драйверов TMC2209 (аппаратный микрошаг 1/16 с интерполяцией StealthChop2 до 1/256)
MICROSTEPPING = 16

# Шкив GT2: 20 зубьев, шаг зуба 2.0 мм -> 40.0 мм за один полный оборот
PULLEY_TEETH = 20
BELT_PITCH_MM = 2.0
MM_PER_REV = PULLEY_TEETH * BELT_PITCH_MM  # 40.0 мм

# Количество микрошагов на 1 мм линейного перемещения:
# (200 * 16) / 40.0 = 80.0 шагов/мм
STEPS_PER_MM = (STEPS_PER_REV * MICROSTEPPING) / MM_PER_REV  # 80.0

# Скорость перемещения по умолчанию для тестовых режимов (мм/с)
DEFAULT_TEST_SPEED_MM_S = 25.0

# Минимальная ширина импульса STEP в микросекундах (TMC2209 требует мин. 100 нс)
STEP_PULSE_DURATION_US = 2


# ==========================================
# 4. Геометрия шахматного поля и рабочей зоны
# ==========================================

# Размер одной клетки поля (мм)
SQUARE_SIZE_MM = 34.0

# Разметка сетки:
# По горизонтали: 12 клеток (2 кладбище слева + 8 шахматная доска A-H + 2 кладбище справа)
GRID_COLS = 12
# По вертикали: 8 клеток (линии 1-8)
GRID_ROWS = 8

GRID_WIDTH_MM = GRID_COLS * SQUARE_SIZE_MM   # 12 * 34 = 408.0 мм
GRID_HEIGHT_MM = GRID_ROWS * SQUARE_SIZE_MM  # 8 * 34 = 272.0 мм

# Полная зона перемещения каретки механики (мм)
# X: от левого края до правого (0 -> 420 мм)
# Y: от верхнего края до нижнего (0 -> 360 мм)
WORK_AREA_X_MM = 420.0
WORK_AREA_Y_MM = 360.0
MAX_X_MM = WORK_AREA_X_MM
MAX_Y_MM = WORK_AREA_Y_MM
DEFAULT_SPEED_MM_S = DEFAULT_TEST_SPEED_MM_S

# Шахматное поле центрировано внутри зоны перемещения:
# Отступ слева и справа: (420 - 408) / 2 = 6.0 мм
# Отступ сверху и снизу: (360 - 272) / 2 = 44.0 мм
OFFSET_X_MM = (WORK_AREA_X_MM - GRID_WIDTH_MM) / 2.0  # 6.0 мм
OFFSET_Y_MM = (WORK_AREA_Y_MM - GRID_HEIGHT_MM) / 2.0  # 44.0 мм
