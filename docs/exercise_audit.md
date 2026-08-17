# Аудит каталога упражнений `leszavr/workout`

Дата: 2026-08-17
Источник: https://github.com/leszavr/workout
Версия (commit): `08e17d7` (2026-08-17)

## Итог

Каталог пригоден для прямого импорта. Данные высокого качества, структура
единообразна, дубликаты отсутствуют. Язык данных — билингвальный (EN + RU).

## Объём данных

| Показатель | Значение |
|---|---|
| Каталог упражнений (`exercises/`) | 873 директории |
| Файлов `exercise.json` | 873 (по одному на упражнение) |
| Изображений | 1746 (ровно 2 на каждое упражнение: `0.jpg`, `1.jpg`) |
| Отсутствующих `exercise.json` | 0 |

Каждое упражнение — отдельная директория вида `exercises/<Name>/`:

```
exercises/3_4_Sit-Up/
├── exercise.json
└── images/
    ├── 0.jpg
    └── 1.jpg
```

## Поля `exercise.json`

Все 11 полей присутствуют во всех 873 записях (100% заполненность):

| Поле | Тип | Описание | Заполненность |
|---|---|---|---|
| `name` | string | Название (EN) | 873/873 |
| `nameRu` | string | Название (RU) | 873/873 |
| `force` | enum | `pull` / `push` / `static` | 873/873 |
| `level` | enum | `beginner` / `intermediate` / `expert` | 873/873 |
| `mechanic` | enum | `compound` / `isolation` | 873/873 |
| `equipment` | string/enum | Оборудование | 873/873 (77 = null) |
| `primaryMuscles` | string[] | Основные мышцы | 873/873 (пустых: 0) |
| `secondaryMuscles` | string[] | Дополнительные мышцы | 873/873 |
| `instructions` | string[] | Техника выполнения (EN), шаги | 868/873 |
| `instructionsRu` | string[] | Техника выполнения (RU), шаги | 868/873 |
| `category` | enum | Категория | 873/873 |

Enum-значения зафиксированы в `types/*.ts` исходного репозитория
(`Muscle`, `Force`, `Level`, `Mechanic`, `Equipment`, `Category`).

## Распределения

### Уровень сложности (`level`)
- beginner: 523
- intermediate: 293
- expert: 57

### Категория (`category`)
- strength: 581
- stretching: 123
- plyometrics: 61
- powerlifting: 38
- olympic weightlifting: 35
- strongman: 21
- cardio: 14

### Оборудование (`equipment`)
- barbell: 170
- dumbbell: 123
- other: 122
- body only: 111
- cable: 81
- **null (не указано): 77**
- machine: 67
- kettlebells: 53
- bands: 20
- medicine ball: 17
- exercise ball: 12
- foam roll: 11
- e-z curl bar: 9

## Качество и проблемы

### Дубликаты
Дубликатов по `name` **нет** (0). Имя директории совпадает с `name`
(с заменой пробелов/спецсимволов на `_`).

### Отсутствующие данные
- **5 упражнений без описания техники** (`instructions` и `instructionsRu` пусты):
  - `Iron_Cross`
  - `One-Arm_Kettlebell_Swings`
  - `Push_Press`
  - `Side_Bridge`
  - `Side_Jackknife`
- **77 упражнений без оборудования** (`equipment = null`).
- Полей «противопоказания» / «ограничения» (`contraindications`, `limitations`)
  в источнике **нет** — их предстоит заполнять отдельно на следующем этапе
  (Safety Rules). При импорте эти поля остаются пустыми списками.

### Изображения
Ровно 2 изображения на упражнение (`0.jpg` — исходное положение,
`1.jpg` — выполнение). Формат JPG. Изображения хранятся локально в репозитории.

## Возможность прямого импорта

**Да, прямой импорт возможен.** Маппинг на внутреннюю модель `Exercise`:

| Источник | Внутреннее поле |
|---|---|
| имя директории | `external_id` (стабильный canonical ID) |
| `name` | `name` |
| `nameRu` | `aliases` / отдельное поле локализации |
| `instructions` / `instructionsRu` | `technique` |
| `primaryMuscles` | `primary_muscles` |
| `secondaryMuscles` | `secondary_muscles` |
| `equipment` | `equipment` |
| `category` | `exercise_type` |
| `level` | `difficulty` |
| `force`, `mechanic` | доп. метаданные |
| `images/0.jpg`, `images/1.jpg` | `images` |
| — | `source = "leszavr/workout"`, `source_version = "08e17d7"` |

Идемпотентность импорта обеспечивается уникальным ключом
`(external_id, source)` — `uq_exercise_external_source`.

## Рекомендации

1. Импортировать все 873 упражнения; 5 записей без техники помечать
   как требующие дополнения (не блокировать импорт).
2. `equipment = null` сохранять как пустой список, не как ошибку.
3. Изображения на этапе импорта не копировать в БД — хранить ссылки
   на путь/URL; фактическую раздачу изображений решить на этапе веб-интерфейса.
4. Противопоказания/ограничения добавить отдельным этапом (Safety Rules).
