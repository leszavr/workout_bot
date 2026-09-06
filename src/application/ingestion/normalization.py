"""Нормализация внешних данных об упражнениях.

Модуль отвечает на один вопрос: как привести название, мышцу и текст техники из
чужого источника к сопоставимому виду, ничего при этом не выдумав.

Три уровня нормализации названия, и они существуют раздельно потому, что решают
разные задачи:

1. ``normalized_name`` — человекочитаемый вид: убраны лишние пробелы и
   форматирование, сохранён порядок слов и все значимые токены. Именно он
   показывается администратору.
2. ``name_key`` — ключ строгого сопоставления. Порядок слов снят, служебные слова
   и разделители убраны, но **все содержательные токены сохранены**. Поэтому
   ``Bench Press - Barbell`` и ``Barbell Bench Press`` дают один ключ, а
   ``Barbell Bench Press - Medium Grip`` — другой: слово ``grip`` содержательно.
3. ``variant_tokens`` — токены, которые в тренировочном смысле различают
   упражнения: хват, стойка, угол, односторонность, снаряд, амплитуда. Они
   собираются отдельно, чтобы matcher мог сказать «то же движение, другой
   вариант» вместо «то же упражнение».

Ключевое решение: значимые токены **не отбрасываются** ради увеличения числа
совпадений. Соблазн отбросить есть — без ``medium grip`` жим средним хватом
совпал бы с обычным жимом и число «уже существующих» выросло бы. Но это
совпадение ложное: средний хват — отдельное упражнение каталога у любого
источника, и объявлять его дублем значит терять данные, объявив это успехом
дедупликации.

Транслитерация решает узкую задачу: источник A даёт названия латиницей,
canonical каталог содержит русские названия, и сопоставлять их напрямую нельзя.
Поэтому русское название приводится к латинице по таблице, а не переводится:
перевод потребовал бы словаря предметных терминов, которого нет, и давал бы
догадку вместо факта.
"""
from __future__ import annotations

import re
import unicodedata

# Служебные слова названий упражнений. Список ограничивает шум, а не смысл:
# каждое слово здесь встречается в названиях как связка и не различает
# упражнения.
#
# Слова `exercise` и `version` в список НЕ входят, хотя выглядят служебными:
# `exercise ball` — это название снаряда (фитбол), и выброшенное `exercise`
# оставило бы неоднозначное `ball`, за которым словарь видит и медбол, и фитбол.
NAME_STOP_WORDS = frozenset(
    {
        "the", "a", "an", "of", "to", "and", "or", "with", "for", "in", "on",
        "at", "by", "from", "your", "his", "her", "its",
        "и", "с", "со", "на", "в", "во", "для", "от", "до", "по",
        "из", "за", "над", "под", "при",
    }
)

# Токены, различающие упражнения в тренировочном смысле. Их присутствие делает
# запись отдельным упражнением, а не вариантом написания: хват, стойка, угол,
# односторонность, снаряд и амплитуда меняют упражнение, а не его название.
VARIANT_TOKENS = frozenset(
    {
        # хват
        "grip", "wide", "narrow", "close", "medium", "neutral", "supinated",
        "pronated", "overhand", "underhand", "reverse", "hammer", "false",
        "mixed", "hook",
        # стойка и положение
        "stance", "sumo", "split", "staggered", "standing", "seated", "lying",
        "prone", "supine", "kneeling", "bent", "over", "incline", "decline",
        "flat", "horizontal", "vertical", "upright", "hanging", "suspended",
        "elevated", "floor", "wall", "bench", "box",
        # угол
        "angle", "angled", "degrees", "degree", "45", "30", "60", "90",
        # односторонность
        "unilateral", "bilateral", "single", "one", "alternate", "alternating",
        "arm", "leg", "side",
        # амплитуда и темп
        "partial", "full", "half", "quarter", "deficit", "pause", "paused",
        "tempo", "explosive", "isometric", "eccentric", "concentric", "pulse",
        "range",
        # снаряд как различитель
        "barbell", "dumbbell", "kettlebell", "cable", "machine", "smith",
        "band", "resistance", "sled", "lever", "leverage", "assisted",
        "weighted", "bodyweight", "bosu", "stability", "ball", "trap", "ez",
        "olympic", "rope", "roller", "wheel", "plate", "landmine", "trx",
        "ring",
        # прочие различители движения
        "behind", "front", "rear", "high", "low", "head", "neck",
        "knee", "hip", "ankle", "toe", "heel",
        "twist", "rotation", "rotational", "jump", "jumping", "walking", "walk",
    }
)

# Различители, которые называют снаряд. Выделены внутри VARIANT_TOKENS, потому
# что отвечают на отдельный вопрос: расхождение по ним может быть расхождением
# формулировки («Bench Press - With Bands» против «Band Bench Press»), а
# расхождение по остальным различителям — расхождением упражнения («Bench Press»
# против «Decline Bench Press»).
#
# Различие используется решением: одинаковые «содержательные» различители при
# совпадающем canonical оборудовании означают одно упражнение под разными
# названиями, а расхождение по содержательным различителям — отдельное
# упражнение. `assisted` и `weighted` в этот набор не входят: облегчение и
# дополнительный вес меняют упражнение, а не его название.
EQUIPMENT_NAMING_TOKENS = frozenset(
    {
        "barbell", "dumbbell", "kettlebell", "cable", "machine", "smith",
        "band", "resistance", "sled", "lever", "leverage", "bosu", "stability",
        "ball", "trap", "ez", "olympic", "roller", "wheel", "plate",
        "landmine", "trx", "ring", "bodyweight",
    }
)

# Названия мышц внутри названия упражнения. Не участвуют ни в ядре движения, ни в
# различителях: мышца — это данные записи (`target`, `primary_muscles`), а не
# часть названия движения, и `Dumbbell Biceps Curl` с `Dumbbell Curl` — одно
# упражнение. Сравнивать мышцы по названию значит сравнивать их дважды и
# получать расхождение там, где поля совпадают.
#
# Список узкий сознательно. В него входят только те мышцы, чьё упоминание в
# названии избыточно при известном движении: подъём на бицепс с гантелями — это и
# есть «Dumbbell Curl», разгибание на трицепс лёжа — «lying extension».
#
# Мышцы, чьё название определяет само движение, в список не входят: `lat` в
# «roller hip lat stretch» отличает растяжку широчайшей от растяжки бедра, `calf`
# и `hamstring` определяют упражнение целиком, а `shoulder` в «shoulder circles» —
# единственное указание на сустав. Стереть их значило бы слить разные упражнения.
#
# Названия движений, в которые входит слово-мышца («chest press», «leg curl»), от
# этого не страдают: они собираются в один токен раньше (MOVEMENT_PHRASES), и
# `Chest Press` не превращается в `Press`.
MUSCLE_NAME_TOKENS = frozenset(
    {
        "abdominal", "abdominals", "abs", "bicep", "biceps", "delt", "deltoid",
        "deltoids", "oblique", "pec", "pecs", "pectoral", "pectorals",
        "tricep", "triceps", "trapezius",
    }
)

# Названия движений из нескольких слов. Собираются в один токен до разделения на
# ядро и различители, потому что иначе аппаратное или мышечное слово внутри
# названия движения читалось бы как различитель: у «Bench Press» слово `bench`
# указывало бы на скамью как на вариант выполнения, а у «Chest Press» слово
# `chest` исчезало бы как название мышцы, и жим от груди совпал бы с жимом над
# головой.
#
# В список входят только названия движений, начинающиеся с существительного —
# снаряда или части тела. Словосочетания, начинающиеся с указания положения
# (`seated row`, `front squat`, `reverse fly`), сюда не входят: `seated` в
# «Seated Cable Row» и в «Seated Row» стоит на разных местах, и сборка фразы
# сработала бы только в одном случае, разведя одинаковые упражнения.
MOVEMENT_PHRASES: tuple[tuple[str, ...], ...] = (
    ("bench", "press"),
    ("bench", "dip"),
    ("leg", "press"),
    ("leg", "curl"),
    ("leg", "extension"),
    ("leg", "raise"),
    ("leg", "lift"),
    ("calf", "raise"),
    ("calf", "press"),
    ("chest", "press"),
    ("chest", "fly"),
    ("chest", "dip"),
    ("shoulder", "press"),
    ("hip", "thrust"),
    ("hip", "extension"),
    ("hip", "abduction"),
    ("hip", "adduction"),
    ("hip", "raise"),
    ("knee", "raise"),
    ("knee", "extension"),
    ("knee", "tuck"),
    ("toe", "touch"),
    ("toe", "raise"),
    ("heel", "raise"),
    ("wall", "sit"),
    ("wall", "slide"),
    ("floor", "press"),
    ("box", "jump"),
    ("jump", "rope"),
    ("face", "pull"),
    ("glute", "bridge"),
    ("wrist", "curl"),
    ("wrist", "extension"),
    ("good", "morning"),
    ("back", "extension"),
    ("back", "raise"),
    ("neck", "extension"),
    ("pull", "up"),
    ("push", "up"),
    ("chin", "up"),
    ("sit", "up"),
    ("step", "up"),
    ("pull", "down"),
    ("push", "down"),
    ("press", "down"),
    ("lat", "pulldown"),
    ("sled", "push"),
    ("sled", "drag"),
    ("farmer", "walk"),
)

_MAX_PHRASE_LENGTH = max(len(phrase) for phrase in MOVEMENT_PHRASES)
_PHRASE_INDEX: dict[tuple[str, ...], str] = {
    phrase: "".join(phrase) for phrase in MOVEMENT_PHRASES
}


# Транслитерация русских названий в латиницу. Нужна для сопоставления с
# англоязычным источником: canonical каталог билингвален, внешний — нет.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y",
    "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Сокращения, которые источники используют вместо полных слов. Раскрываются, а
# не отбрасываются: `DB Curl` и `Dumbbell Curl` — одно упражнение, и без
# раскрытия они не совпадут.
ABBREVIATIONS = {
    "db": "dumbbell",
    "bb": "barbell",
    "kb": "kettlebell",
    "ez": "ez",
    "sl": "single leg",
    "ohp": "overhead press",
    "rdl": "romanian deadlift",
    "bw": "bodyweight",
    "gm": "good morning",
    "ghr": "glute ham raise",
    "bosu": "bosu",
    "v-bar": "v bar",
    "t-bar": "t bar",
    "sldl": "stiff leg deadlift",
    "bosuball": "bosu ball",
}

# Замены слитных и дефисных форм одного понятия. Список закрывает расхождение
# написания, а не смысла.
_WORD_SYNONYMS = {
    "pushup": "push up",
    "pushups": "push up",
    "push-up": "push up",
    "push-ups": "push up",
    "pullup": "pull up",
    "pullups": "pull up",
    "pull-up": "pull up",
    "pull-ups": "pull up",
    "chinup": "chin up",
    "chin-up": "chin up",
    "situp": "sit up",
    "sit-up": "sit up",
    "situps": "sit up",
    "signup": "sit up",
    "stepup": "step up",
    "step-up": "step up",
    "lunges": "lunge",
    "curls": "curl",
    "raises": "raise",
    "presses": "press",
    "extensions": "extension",
    "rows": "row",
    "squats": "squat",
    "deadlifts": "deadlift",
    "dips": "dip",
    "crunches": "crunch",
    "flyes": "fly",
    "flys": "fly",
    "flies": "fly",
    "abs": "abdominals",
    "quads": "quadriceps",
    "pecs": "pectorals",
    "lats": "lats",
    "delts": "deltoids",
    "tricep": "triceps",
    "bicep": "biceps",
}

# Mojibake, встреченное в источнике: `в°` — это испорченный знак градуса. Замена
# выполняется на нормализации, а не правкой источника: источник читается только
# для чтения, и его дефекты остаются видимы в raw_name.
_MOJIBAKE = {
    "в°": " degrees ",
    "°": " degrees ",
    "\ufffd": " ",
}

_NON_WORD = re.compile(r"[^0-9a-zа-яё]+")

# Пометки подачи материала, а не упражнения. Источник использует их для описания
# съёмки и модели: `(male)`, `(female)`, `(back pov)`, `v. 2`. Из ключа
# сопоставления они убираются, иначе `barbell full squat` и
# `barbell full squat (male)` считались бы двумя разными упражнениями — а это
# один и тот же присед, снятый с другой моделью.
#
# В `raw_name` пометка сохраняется: она остаётся видна администратору и
# показывает, откуда взялась запись.
_PRESENTATION_MARKERS = re.compile(
    r"\((?:male|female|(?:back|side|front|top|bottom)\s+pov)\)|\bv\.\s*\d+\b",
    re.IGNORECASE,
)


def strip_presentation_markers(value: str) -> str:
    """Убирает пометки съёмки и модели из названия."""
    return " ".join(_PRESENTATION_MARKERS.sub(" ", value).split())



def transliterate(value: str) -> str:
    """Переводит русские буквы в латиницу по таблице.

    Это транслитерация, а не перевод: `Присед` становится `prised`, и совпадение
    с `squat` не возникает. Задача другая — сопоставить русские названия
    canonical каталога между собой и с русскими названиями источника, не завися
    от регистра и «ё».
    """
    lowered = value.lower().replace("ё", "е")
    return "".join(_TRANSLIT.get(char, char) for char in lowered)


def clean_text(value: str | None) -> str:
    """Убирает управляющие символы, mojibake и лишние пробелы."""
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value)
    for broken, replacement in _MOJIBAKE.items():
        text = text.replace(broken, replacement)
    text = "".join(char if char.isprintable() or char == "\n" else " " for char in text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def normalize_name(value: str) -> str:
    """Человекочитаемое нормализованное название.

    Порядок слов и содержательные токены сохраняются: это то, что показывается
    администратору, и переставленные слова читались бы как ошибка импорта.
    """
    text = clean_text(value)
    text = text.replace("/", " / ")
    text = " ".join(text.split())
    return text[:255]


# Слова, которые в названиях caталога остаются со строчной буквы: служебные
# части речи внутри названия. Список короткий и включает только бесспорные
# предлоги и союзы.
_LOWERCASE_IN_TITLE = frozenset(
    {"and", "or", "the", "a", "an", "of", "to", "on", "in", "with", "at", "for"}
)

# Аббревиатуры, которые пишутся заглавными целиком. Список нужен потому, что
# правило «первая буква заглавная» превратило бы `EZ` в `Ez`, а `TRX` в `Trx` —
# и название стало бы неузнаваемым.
_UPPERCASE_WORDS = frozenset({"ez", "trx", "ghr", "rdl", "ohp", "bosu", "pov", "v"})


def display_name(value: str) -> str:
    """Приводит название к регистру caталога.

    Внешний источник пишет названия строчными («barbell bench press»),
    действующий каталог — с заглавных («Barbell Bench Press»). Разница не
    косметическая, и это выяснилось на проверке генерации: детерминированный
    генератор сортирует упражнения по названию, а в Python строчные буквы идут
    после заглавных — все импортированные упражнения оказывались в конце каждой
    группы. Причина устранена с двух сторон: сортировка сделана
    регистронезависимой, а названия приведены к единому виду, потому что каталог
    показывается человеку и два стиля в одном списке читаются как дефект.

    Регистр меняется только у первой буквы слова: `sit-up` → `Sit-up`, а не
    `Sit-Up`. Единого правила для дефисных частей у caталога нет (`Pull-Up`,
    `T-Bar Row`), и выдумывать его здесь не требуется.
    """
    normalized = normalize_name(value)
    result: list[str] = []
    for index, word in enumerate(normalized.split(" ")):
        if not word:
            continue
        # Ведущая пунктуация не мешает распознать слово: `(on` — это `on`.
        prefix_length = 0
        while prefix_length < len(word) and not word[prefix_length].isalnum():
            prefix_length += 1
        prefix, body = word[:prefix_length], word[prefix_length:]
        if not body:
            result.append(word)
            continue
        stripped = body.rstrip("".join(c for c in body if not c.isalnum()))
        lowered = body.lower()
        if body.isupper() and len(body) > 1:
            result.append(word)
        elif lowered.strip(").,") in _UPPERCASE_WORDS:
            result.append(prefix + body.upper())
        elif index > 0 and lowered.strip(").,") in _LOWERCASE_IN_TITLE:
            result.append(prefix + lowered)
        else:
            result.append(prefix + body[:1].upper() + body[1:])
        del stripped
    return " ".join(result)[:255]


def tokenize(value: str) -> list[str]:
    """Значимые токены названия: раскрытые сокращения, без служебных слов.

    Названия движений из нескольких слов собираются в один токен последним шагом:
    иначе `bench` в «Bench Press» читалось бы как указание на скамью, а `chest` в
    «Chest Press» исчезало бы как название мышцы, и жим от груди совпал бы с
    жимом над головой.
    """
    text = clean_text(value).lower().replace("ё", "е")
    for broken, replacement in _MOJIBAKE.items():
        text = text.replace(broken, replacement)
    text = strip_presentation_markers(text)
    raw_tokens = [t for t in _NON_WORD.split(text) if t]

    expanded: list[str] = []
    for token in raw_tokens:
        replacement = ABBREVIATIONS.get(token) or _WORD_SYNONYMS.get(token)
        if replacement:
            expanded.extend(replacement.split())
        else:
            expanded.append(token)

    singular: list[str] = []
    for token in expanded:
        if token in NAME_STOP_WORDS:
            continue
        # Единственное число для регулярных английских форм: `curls` → `curl`.
        # Правило узкое (только `s` после согласной), потому что широкое
        # склеивало бы `press` и `pres`.
        if len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
            reduced = token[:-1]
            token = _WORD_SYNONYMS.get(reduced, reduced)
        singular.append(token)

    return _collapse_phrases(singular)


def _collapse_phrases(tokens: list[str]) -> list[str]:
    """Собирает известные названия движений в один токен."""
    result: list[str] = []
    index = 0
    total = len(tokens)
    while index < total:
        matched = False
        for length in range(min(_MAX_PHRASE_LENGTH, total - index), 1, -1):
            phrase = tuple(tokens[index : index + length])
            collapsed = _PHRASE_INDEX.get(phrase)
            if collapsed is not None:
                result.append(collapsed)
                index += length
                matched = True
                break
        if not matched:
            result.append(tokens[index])
            index += 1
    return result


def name_key(value: str) -> str:
    """Ключ строгого сопоставления: отсортированные значимые токены.

    Сортировка снимает различие порядка слов (`Bench Press - Barbell` против
    `Barbell Bench Press`), но ни один содержательный токен не удаляется:
    `medium grip` остаётся частью ключа, и жим средним хватом не сливается с
    обычным жимом.
    """
    tokens = tokenize(value)
    if not tokens:
        return ""
    return " ".join(sorted(set(tokens)))[:255]


def latin_name_key(value: str) -> str:
    """Ключ сопоставления для русских названий: транслитерация плюс ключ."""
    return name_key(transliterate(value))


def variant_tokens(value: str) -> frozenset[str]:
    """Токены, различающие упражнения в тренировочном смысле."""
    return frozenset(t for t in tokenize(value) if t in VARIANT_TOKENS)


def core_tokens(value: str) -> frozenset[str]:
    """Токены движения без различителей варианта и названий мышц.

    Нужны, чтобы отличить «то же движение, другой вариант» от «другое
    упражнение»: у жима штанги и жима штанги средним хватом ядро совпадает, у
    жима и разведения рук — нет.

    Названия мышц из ядра исключены: мышца записана полем (`target`,
    `primary_muscles`), и присутствие её названия в тексте ничего не добавляет, а
    вот отсутствие — разводит одинаковые упражнения (`Dumbbell Biceps Curl`
    против `Dumbbell Curl`).
    """
    return frozenset(
        t
        for t in tokenize(value)
        if t not in VARIANT_TOKENS and t not in MUSCLE_NAME_TOKENS
    )


def equipment_naming_tokens(value: str) -> frozenset[str]:
    """Различители названия, указывающие на снаряд."""
    return frozenset(t for t in tokenize(value) if t in EQUIPMENT_NAMING_TOKENS)


def equipment_words(equipment_ids: frozenset[str]) -> frozenset[str]:
    """Слова canonical идентификаторов оборудования записи.

    Нужны, чтобы понять, объясняет ли объявленное оборудование слово в названии:
    `Bench Press - With Bands` при `equipment = bands` даёт canonical
    `resistance_band`, и слово `band` в названии этим объяснено. Сравнение идёт по
    словам canonical ID, а не по формулировке источника, потому что источники
    называют один снаряд разными словами (`bands` и `resistance band`, `cable` и
    `cable machine`).
    """
    words: set[str] = set()
    for equipment_id in equipment_ids:
        words.update(tokenize(equipment_id.replace("_", " ")))
    return frozenset(words)


def naming_equipment_words(value: str) -> frozenset[str]:
    """Слова названия, обозначающие снаряд."""
    return frozenset(t for t in tokenize(value) if t in EQUIPMENT_NAMING_TOKENS)


def naming_equipment_phrases(value: str) -> list[str]:
    """Фразы названия, которые могут обозначать оборудование.

    Возвращает отдельные слова-снаряды и пары соседних слов, содержащие такое
    слово. Пары обязательны: словарь знает `stability ball` и `medicine ball`, но
    не знает `ball` отдельно — и правильно, потому что `ball` сам по себе
    неоднозначен.

    Разрешать фразы словарём оборудования, а не собственным списком, — то же
    решение, что и в прошлом этапе: синонимы живут в данных, и добавление снаряда
    не требует правки кода.
    """
    tokens = tokenize(value)
    phrases: list[str] = []
    for index, token in enumerate(tokens):
        if token in EQUIPMENT_NAMING_TOKENS:
            phrases.append(token)
        if index + 1 < len(tokens):
            pair = (token, tokens[index + 1])
            if any(word in EQUIPMENT_NAMING_TOKENS for word in pair):
                phrases.append(" ".join(pair))
    return phrases


def semantic_variant_tokens(value: str) -> frozenset[str]:
    """Различители способа выполнения: хват, угол, стойка, амплитуда, вес.

    Слова, называющие снаряд, сюда не входят: один и тот же снаряд источники
    называют разными словами (`band` и `bands`, `dumbbell` и `dumbbells`), и
    разница в слове не является разницей упражнения.

    Снаряд, названный в дополнение к объявленному оборудованию, различителем всё
    же становится, но выясняется это сравнением с объявленным полем, а не по
    слову: см. ``variant_signature`` в ``src/application/ingestion/matching.py``.
    """
    return frozenset(
        t
        for t in tokenize(value)
        if t in VARIANT_TOKENS and t not in EQUIPMENT_NAMING_TOKENS
    )


def steps_to_technique(steps: list[str]) -> str | None:
    """Превращает шаги источника в нумерованный текст техники.

    Формат совпадает с существующим импортом каталога
    (`scripts/import_exercises.py`): техника упражнения обязана выглядеть
    одинаково независимо от источника, иначе HTML-программа показывала бы два
    разных формата.
    """
    cleaned = [clean_text(step) for step in steps]
    cleaned = [step for step in cleaned if step]
    if not cleaned:
        return None
    return "\n".join(f"{index + 1}. {step}" for index, step in enumerate(cleaned))
