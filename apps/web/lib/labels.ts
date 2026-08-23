// Русские подписи для значений, приходящих из API.
//
// Правило: в интерфейсе нет английских слов и транслитерации. Здесь единый
// глоссарий, чтобы одно и то же понятие называлось одинаково во всех разделах.
//
// Соответствие доменных терминов и того, что видит пользователь:
//   provider  → сервис ИИ
//   endpoint  → подключение
//   task      → задача
//   prompt    → инструкция
//   fallback  → резервный (алгоритмический) генератор
//   readiness → готовность
//   health    → состояние

export const STATUS_LABELS: Record<string, string> = {
  draft: "черновик",
  // Анкета и программа — женского рода, подписи согласованы по нему.
  confirmed: "подтверждена",
  in_progress: "заполняется",
  generated: "создана",
  validated: "проверена",
  active: "активна",
  archived: "в архиве",
  failed: "ошибка",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

/** Тон для статуса профиля или программы. */
export function statusTone(status: string): "ok" | "warn" | "bad" | "neutral" {
  if (status === "confirmed" || status === "active" || status === "validated") return "ok";
  if (status === "in_progress" || status === "generated") return "warn";
  if (status === "failed") return "bad";
  return "neutral";
}

export const GENERATION_SOURCE_LABELS: Record<string, string> = {
  // «Алгоритмический» вместо «deterministic»: программа собирается по
  // правилам подбора, без обращения к ИИ.
  deterministic: "алгоритмический",
  ai: "ИИ",
  manual: "вручную",
};

export function generationSourceLabel(source: string): string {
  return GENERATION_SOURCE_LABELS[source] ?? source;
}

export const EXERCISE_TYPE_LABELS: Record<string, string> = {
  strength: "Силовые",
  stretching: "Растяжка",
  plyometrics: "Плиометрика",
  powerlifting: "Пауэрлифтинг",
  "olympic weightlifting": "Тяжёлая атлетика",
  strongman: "Стронгмен",
  cardio: "Кардио",
};

export const FORCE_LABELS: Record<string, string> = {
  push: "жим",
  pull: "тяга",
  static: "статика",
};

export const MECHANIC_LABELS: Record<string, string> = {
  compound: "базовое",
  isolation: "изолирующее",
};

export const DIFFICULTY_LABELS: Record<string, string> = {
  beginner: "Начальный",
  intermediate: "Средний",
  expert: "Продвинутый",
};

// --- Каталог упражнений ------------------------------------------------------
//
// Справочник упражнений загружен из открытой английской базы, поэтому
// оборудование и мышцы хранятся английскими тегами. Наборы закрытые:
// см. CATALOG_EQUIPMENT в src/application/programs/filtering.py.

export const EQUIPMENT_LABELS: Record<string, string> = {
  "body only": "без оборудования",
  bands: "резиновые петли",
  barbell: "штанга",
  cable: "блочный тренажёр",
  dumbbell: "гантели",
  "e-z curl bar": "изогнутый гриф",
  "exercise ball": "фитбол",
  "foam roll": "массажный валик",
  kettlebells: "гири",
  machine: "тренажёр",
  "medicine ball": "медбол",
  other: "другое",
};

export const MUSCLE_LABELS: Record<string, string> = {
  abdominals: "живот",
  abductors: "отводящие мышцы бедра",
  adductors: "приводящие мышцы бедра",
  biceps: "бицепс",
  calves: "икры",
  chest: "грудь",
  forearms: "предплечья",
  glutes: "ягодицы",
  hamstrings: "задняя поверхность бедра",
  lats: "широчайшие",
  "lower back": "низ спины",
  "middle back": "середина спины",
  neck: "шея",
  quadriceps: "передняя поверхность бедра",
  shoulders: "плечи",
  traps: "трапеции",
  triceps: "трицепс",
};

/** Список тегов каталога русскими словами; «—», если список пуст. */
export function catalogList(
  values: readonly string[],
  dictionary: Record<string, string>,
): string {
  const parts = values.map((value) => dictionary[value] ?? value);
  return parts.join(", ") || "—";
}

export function equipmentList(values: readonly string[]): string {
  return catalogList(values, EQUIPMENT_LABELS);
}

export function muscleList(values: readonly string[]): string {
  return catalogList(values, MUSCLE_LABELS);
}

// --- Значения анкеты ---------------------------------------------------------
//
// Бот сохраняет ответы кодами (`male`, `gym`, `over_1_year`), и API отдаёт
// анкету как есть. Подписи должны совпадать с серверными: они лежат в
// src/application/questionnaire/labels.py и используются в сводке для клиента.

export const QUESTIONNAIRE_LABELS: Record<string, string> = {
  male: "Мужской",
  female: "Женский",
  not_specified: "Не указан",

  weight_loss: "Снижение веса",
  muscle_gain: "Набор мышечной массы",
  strength: "Увеличение силы",
  health_fitness: "Здоровье и общая форма",
  endurance: "Повышение выносливости",
  return_to_training: "Возвращение к тренировкам",
  other: "Другое",

  never: "Никогда не занимался",
  long_break: "Был длинный перерыв",
  under_3_months: "До 3 месяцев",
  "3_12_months": "3–12 месяцев",
  over_1_year: "Больше года",

  "1_month": "1 месяц",
  "2_3_months": "2–3 месяца",
  "3_6_months": "3–6 месяцев",
  "6_12_months": "6–12 месяцев",
  no_rush: "Не тороплюсь",

  home: "Дома",
  gym: "В зале",
  both: "Дома и в зале",

  morning: "Утро",
  afternoon: "День",
  evening: "Вечер",
  any: "Любое время",

  sedentary: "Сидячая работа, мало движения",
  light_walking: "Немного хожу в течение дня",
  active_walking: "Много хожу или активная работа",
  physical_work: "Тяжёлая физическая работа",
  very_active: "Очень высокая активность",

  love: "Нравится",
  okay: "Нормально отношусь",
  dislike: "Не люблю",
  exclude: "Не хочу",
  walking_only: "Только ходьба",

  mon: "Пн",
  tue: "Вт",
  wed: "Ср",
  thu: "Чт",
  fri: "Пт",
  sat: "Сб",
  sun: "Вс",
};

/**
 * Подпись ответа анкеты. Незнакомое значение возвращается как есть: в
 * большинстве полей человек пишет свободным текстом, и его нельзя терять.
 */
export function questionnaireLabel(value: string): string {
  return QUESTIONNAIRE_LABELS[value] ?? value;
}

export const CONSENT_LABELS: Record<string, string> = {
  data_processing: "Обработка персональных данных",
  health_information: "Использование данных о здоровье",
  accuracy: "Достоверность указанных сведений",
};

export function consentLabel(scope: string): string {
  return CONSENT_LABELS[scope] ?? scope;
}

// --- Задачи ИИ ---------------------------------------------------------------

export const AI_TASK_LABELS: Record<string, string> = {
  workout_generation: "Создание программы тренировок",
};

export function aiTaskLabel(taskType: string): string {
  return AI_TASK_LABELS[taskType] ?? taskType;
}

/** Зачем нужна задача: показывается рядом с её настройками. */
export const AI_TASK_HINTS: Record<string, string> = {
  workout_generation:
    "Единственное место, где система обращается к ИИ. Если задача выключена " +
    "или ИИ недоступен, программу соберёт алгоритмический генератор.",
};

export function aiTaskHint(taskType: string): string {
  return AI_TASK_HINTS[taskType] ?? "";
}

// --- Готовность --------------------------------------------------------------

export const AI_READINESS_STATUS_LABELS: Record<string, string> = {
  ok: "готово",
  warning: "обратите внимание",
  missing: "не настроено",
  failed: "ошибка",
};

export function aiReadinessStatusLabel(status: string): string {
  return AI_READINESS_STATUS_LABELS[status] ?? status;
}

/** Тон для компонента Status по статусу шага готовности. */
export function readinessTone(status: string): "ok" | "warn" | "bad" | "neutral" {
  if (status === "ok") return "ok";
  if (status === "warning") return "warn";
  if (status === "failed") return "bad";
  return "neutral";
}

// --- Состояние сервисов и моделей ---------------------------------------------

export const AI_HEALTH_LABELS: Record<string, string> = {
  healthy: "работает",
  degraded: "работает с перебоями",
  unavailable: "недоступен",
  not_tested: "не проверялся",
  disabled: "выключен",
  unsupported: "не поддерживается",
};

export function aiHealthLabel(state: string): string {
  return AI_HEALTH_LABELS[state] ?? state;
}

export const AI_AVAILABILITY_LABELS: Record<string, string> = {
  available: "доступна",
  degraded: "с перебоями",
  unavailable: "недоступна",
  not_tested: "не проверялась",
  disabled: "выключена",
  unsupported: "не поддерживается",
};

export function aiAvailabilityLabel(state: string): string {
  return AI_AVAILABILITY_LABELS[state] ?? state;
}

/** Тон для состояния сервиса, подключения или модели. */
export function healthTone(state: string): "ok" | "warn" | "bad" | "neutral" {
  if (state === "healthy" || state === "available") return "ok";
  if (state === "degraded") return "warn";
  if (state === "unavailable" || state === "unsupported") return "bad";
  return "neutral";
}

// --- Причины перехода на резервный генератор ----------------------------------

export const AI_FALLBACK_REASON_LABELS: Record<string, string> = {
  // Настройка: обращения к ИИ не было.
  ai_not_configured: "ИИ не настроен",
  provider_unavailable: "сервис выключен",
  endpoint_unavailable: "подключение недоступно",
  connection_not_tested: "подключение не проверялось",
  model_unavailable: "модель недоступна",
  unsupported_protocol: "способ подключения не поддерживается",
  task_disabled: "задача выключена",
  task_not_ready: "задача не готова",
  generator_not_configured: "генератор не настроен",
  // Обращение было и не удалось.
  ai_timeout: "ИИ не ответил вовремя",
  ai_runtime_failure: "сбой при обращении к ИИ",
  ai_invalid_response: "ИИ вернул некорректный ответ",
  ai_validation_failed: "ответ ИИ не прошёл проверку",
};

export function aiFallbackReasonLabel(reason: string): string {
  return AI_FALLBACK_REASON_LABELS[reason] ?? reason;
}

// --- Журналы -----------------------------------------------------------------

export const AI_AUDIT_EVENT_LABELS: Record<string, string> = {
  ai_provider_created: "сервис добавлен",
  ai_provider_updated: "сервис изменён",
  ai_provider_deleted: "сервис удалён",
  ai_endpoint_created: "подключение добавлено",
  ai_endpoint_updated: "подключение изменено",
  ai_endpoint_deleted: "подключение удалено",
  ai_endpoint_secret_rotated: "ключ доступа обновлён",
  ai_model_created: "модель добавлена",
  ai_model_updated: "модель изменена",
  ai_model_deleted: "модель удалена",
  ai_task_updated: "настройки задачи сохранены",
  ai_prompt_created: "создана версия инструкции",
  ai_generation_fallback: "программа собрана без ИИ",
  admin_user_created: "пользователь создан",
  admin_user_updated: "пользователь изменён",
  admin_user_deleted: "пользователь удалён",
  admin_user_password_changed: "пароль изменён",
  admin_user_password_reset: "пароль сброшен",
};

export function aiAuditEventLabel(eventType: string): string {
  return AI_AUDIT_EVENT_LABELS[eventType] ?? eventType;
}

export const AI_USAGE_STATUS_LABELS: Record<string, string> = {
  success: "успешно",
  error: "ошибка",
};

export function aiUsageStatusLabel(status: string): string {
  return AI_USAGE_STATUS_LABELS[status] ?? status;
}

export const GENERATOR_LABELS: Record<string, string> = {
  ai: "ИИ",
  deterministic: "алгоритмический",
};

export function generatorLabel(generator: string): string {
  return GENERATOR_LABELS[generator] ?? generator;
}

// --- Роли --------------------------------------------------------------------

export const ROLE_LABELS: Record<string, string> = {
  admin: "администратор",
  viewer: "наблюдатель",
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

export const ROLE_HINTS: Record<string, string> = {
  admin: "Полный доступ: настройки, создание программ, управление пользователями.",
  viewer: "Только просмотр данных, изменения недоступны.",
};

export function roleHint(role: string): string {
  return ROLE_HINTS[role] ?? "";
}
