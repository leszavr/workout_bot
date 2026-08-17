# Workout Bot

Telegram-бот для сбора анкеты клиента и формирования файла `client_profile.json`.

## Запуск

```bash
export BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
python bot.py
```

## Структура

- `bot.py` — точка входа
- `handlers/` — обработчики команд и шагов анкеты
- `states/` — FSM состояния
- `services/` — сохранение профиля и построение JSON
- `data/` — директория с профилями, фото и логами
