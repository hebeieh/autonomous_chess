/**
 * Autonomous Chess — Web Dashboard Frontend Script
 * Real-time SSE listener + Interactive Motion & Mock Controls
 */

document.addEventListener("DOMContentLoaded", () => {
    // Конфигурация и состояние
    let currentStepMm = 10;
    let isMockMcp = document.body.dataset.mockMcp === "true";
    let isDryRun = document.body.dataset.dryRun === "true";
    let activeSquaresSet = new Set();
    let isJogging = false;

    // DOM Элементы
    const activeCountEl = document.getElementById("active-count");
    const activePillsEl = document.getElementById("active-pills-list");
    const pollRateEl = document.getElementById("poll-rate");
    const posXEl = document.getElementById("pos-x");
    const posYEl = document.getElementById("pos-y");
    const statusMagnetEl = document.getElementById("status-magnet");
    const statusMotorsEl = document.getElementById("status-motors");
    const alertBox = document.getElementById("action-alert");
    const alertText = document.getElementById("alert-text");

    const btnToggleMagnet = document.getElementById("btn-toggle-magnet");
    const btnTogglePower = document.getElementById("btn-toggle-power");
    const btnHoming = document.getElementById("btn-homing");
    const stepButtons = document.querySelectorAll(".btn-step");
    const jogButtons = document.querySelectorAll(".btn-jog");

    const btnMockStandard = document.getElementById("btn-mock-standard");
    const btnMockClear = document.getElementById("btn-mock-clear");

    // =========================================================================
    // Вспомогательные функции
    // =========================================================================
    function showAlert(msg, isError = false) {
        if (!alertText || !alertBox) return;
        alertText.textContent = msg;
        alertBox.style.borderColor = isError ? "var(--accent-red)" : "var(--accent-cyan)";
        alertBox.style.color = isError ? "var(--accent-red)" : "var(--accent-cyan)";
    }

    async function sendPost(url, payload = {}) {
        try {
            const resp = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await resp.json();
            if (!resp.ok) {
                throw new Error(data.message || `Ошибка сервера: ${resp.status}`);
            }
            return data;
        } catch (err) {
            showAlert(err.message, true);
            console.error(err);
            throw err;
        }
    }

    // =========================================================================
    // Server-Sent Events (SSE) поток
    // =========================================================================
    function initSSE() {
        const evtSource = new EventSource("/api/stream");

        evtSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                updateDashboard(data);
            } catch (e) {
                console.error("Ошибка парсинга SSE сообщения:", e);
            }
        };

        evtSource.onerror = (err) => {
            console.warn("Потеряно соединение с сервером SSE, переподключение...", err);
            pollRateEl.textContent = "0.0 Гц";
        };
    }

    function updateDashboard(data) {
        // 1. Обновление частоты опроса
        if (data.scan_hz !== undefined) {
            pollRateEl.textContent = `${data.scan_hz} Гц`;
        }

        // 2. Обновление подсветки шахматных клеток
        if (data.active_squares) {
            const newSquares = new Set(data.active_squares);
            
            // Снимаем активность с клеток, которых больше нет
            activeSquaresSet.forEach((sq) => {
                if (!newSquares.has(sq)) {
                    const el = document.getElementById(`sq-${sq}`);
                    if (el) el.classList.remove("active");
                }
            });

            // Добавляем активность новым клеткам
            newSquares.forEach((sq) => {
                if (!activeSquaresSet.has(sq)) {
                    const el = document.getElementById(`sq-${sq}`);
                    if (el) el.classList.add("active");
                }
            });

            activeSquaresSet = newSquares;

            // Обновляем счетчик
            if (activeCountEl) {
                activeCountEl.textContent = data.active_squares.length;
            }

            // Обновляем бейджи координат
            if (activePillsEl) {
                if (data.active_squares.length === 0) {
                    activePillsEl.innerHTML = '<span class="no-sensors-msg">Магниты не обнаружены</span>';
                } else {
                    activePillsEl.innerHTML = data.active_squares
                        .map((sq) => `<span class="sq-pill">${sq.toUpperCase()}</span>`)
                        .join("");
                }
            }
        }

        // 3. Телеметрия приводов
        if (data.position) {
            if (posXEl) posXEl.innerHTML = `${data.position.x.toFixed(1)} <small>мм</small>`;
            if (posYEl) posYEl.innerHTML = `${data.position.y.toFixed(1)} <small>мм</small>`;
        }

        if (data.magnet_active !== undefined) {
            if (statusMagnetEl) {
                statusMagnetEl.textContent = data.magnet_active ? "ВКЛ" : "ВЫКЛ";
                statusMagnetEl.className = data.magnet_active ? "item-val alert-state" : "item-val";
            }
            if (btnToggleMagnet) {
                btnToggleMagnet.classList.toggle("active-magnet", data.magnet_active);
            }
        }

        if (data.motors_enabled !== undefined) {
            if (statusMotorsEl) {
                statusMotorsEl.textContent = data.motors_enabled ? "ВКЛ" : "ВЫКЛ";
                statusMotorsEl.className = data.motors_enabled ? "item-val active-state" : "item-val";
            }
            if (btnTogglePower) {
                btnTogglePower.classList.toggle("active-power", data.motors_enabled);
            }
        }
    }

    // =========================================================================
    // Управление перемещением (Jog)
    // =========================================================================
    async function executeJog(dxFactor, dyFactor) {
        if (isJogging) return;
        isJogging = true;

        const dx = dxFactor * currentStepMm;
        const dy = dyFactor * currentStepMm;

        try {
            showAlert(`Перемещение: dX=${dx.toFixed(1)} мм, dY=${dy.toFixed(1)} мм...`);
            const res = await sendPost("/api/motor/jog", { dx, dy });
            if (res.status === "ok") {
                showAlert(`Позиция: X=${res.position.x.toFixed(1)} мм, Y=${res.position.y.toFixed(1)} мм`);
            }
        } catch (e) {
            // Ошибка уже выведена в showAlert
        } finally {
            isJogging = false;
        }
    }

    // Выбор шага
    stepButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
            stepButtons.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            currentStepMm = parseFloat(btn.dataset.step) || 10;
            showAlert(`Выбран шаг: ${currentStepMm} мм`);
        });
    });

    // Кнопки D-pad
    jogButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
            const dxFactor = parseFloat(btn.dataset.dx) || 0;
            const dyFactor = parseFloat(btn.dataset.dy) || 0;
            executeJog(dxFactor, dyFactor);
        });
    });

    // Homing
    if (btnHoming) {
        btnHoming.addEventListener("click", async () => {
            try {
                showAlert("Запуск StallGuard Sensorless Homing...");
                const res = await sendPost("/api/motor/homing");
                showAlert(res.message || "Homing успешно завершен!");
            } catch (e) {}
        });
    }

    // Toggle Magnet
    if (btnToggleMagnet) {
        btnToggleMagnet.addEventListener("click", async () => {
            try {
                const res = await sendPost("/api/magnet");
                showAlert(res.magnet_active ? "Электромагнит ВКЛЮЧЕН" : "Электромагнит ВЫКЛЮЧЕН");
            } catch (e) {}
        });
    }

    // Toggle Motor Power
    if (btnTogglePower) {
        btnTogglePower.addEventListener("click", async () => {
            try {
                const res = await sendPost("/api/motor/power");
                showAlert(res.motors_enabled ? "Обмотки моторов запитаны" : "Обмотки моторов обесточены");
            } catch (e) {}
        });
    }

    // =========================================================================
    // Клик по клеткам в Mock-режиме
    // =========================================================================
    document.querySelectorAll(".cell").forEach((cell) => {
        cell.addEventListener("click", async () => {
            if (!isMockMcp) return;
            const sq = cell.dataset.square;
            if (!sq) return;

            try {
                const res = await sendPost("/api/mock/toggle", { square: sq });
                showAlert(`Клетка ${sq.toUpperCase()}: ${res.active ? "Установлена фигура" : "Фигура снята"}`);
            } catch (e) {}
        });
    });

    if (btnMockStandard) {
        btnMockStandard.addEventListener("click", async () => {
            try {
                showAlert("Расстановка стартовой позиции...");
                await sendPost("/api/mock/reset", { mode: "standard" });
                showAlert("Установлены 32 стартовые фигуры!");
            } catch (e) {}
        });
    }

    if (btnMockClear) {
        btnMockClear.addEventListener("click", async () => {
            try {
                showAlert("Очистка доски...");
                await sendPost("/api/mock/reset", { mode: "clear" });
                showAlert("Доска очищена!");
            } catch (e) {}
        });
    }

    // =========================================================================
    // Обработка клавиатуры (WASD, QEZC, Space, P, H)
    // =========================================================================
    window.addEventListener("keydown", (evt) => {
        // Игнорируем ввод, если фокус в поле ввода (если появятся)
        if (evt.target.tagName === "INPUT" || evt.target.tagName === "TEXTAREA") return;

        const key = evt.key.toLowerCase();

        switch (key) {
            // Ортогональные направления
            case "w":
            case "ц":
                evt.preventDefault();
                executeJog(0, 1);
                break;
            case "s":
            case "ы":
                evt.preventDefault();
                executeJog(0, -1);
                break;
            case "a":
            case "ф":
                evt.preventDefault();
                executeJog(-1, 0);
                break;
            case "d":
            case "в":
                evt.preventDefault();
                executeJog(1, 0);
                break;

            // Диагональные направления
            case "q":
            case "й":
                evt.preventDefault();
                executeJog(-1, 1);
                break;
            case "e":
            case "у":
                evt.preventDefault();
                executeJog(1, 1);
                break;
            case "z":
            case "я":
                evt.preventDefault();
                executeJog(-1, -1);
                break;
            case "c":
            case "с":
                evt.preventDefault();
                executeJog(1, -1);
                break;

            // Аппаратные действия
            case " ":
                evt.preventDefault();
                if (btnToggleMagnet) btnToggleMagnet.click();
                break;
            case "p":
            case "з":
                evt.preventDefault();
                if (btnTogglePower) btnTogglePower.click();
                break;
            case "h":
            case "р":
                evt.preventDefault();
                if (btnHoming) btnHoming.click();
                break;
        }
    });

    // Запуск SSE
    initSSE();
});
