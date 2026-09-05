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

// --- База знаний об оборудовании ---------------------------------------------
//
// Названия оборудования и возможностей приходят из базы вместе с записью
// (`name_ru`), поэтому словаря подписей здесь нет: словарь пополняется через
// админку, и дублировать его в коде значило бы гарантированно с ним разойтись.
// Здесь только подписи закрытых наборов — статусов и типов, определённых кодом.

export const COMPATIBILITY_LABELS: Record<string, string> = {
  compatible: "подходит",
  incompatible: "не подходит",
  unknown: "неизвестно",
};

export const COMPATIBILITY_REASON_LABELS: Record<string, string> = {
  no_equipment_needed: "оборудование не требуется",
  all_required_available: "всё необходимое есть",
  alternative_equipment_available: "подходит один из вариантов",
  specialized_equipment_available: "подходит имеющийся тренажёр этого вида",
  required_equipment_missing: "нет обязательного оборудования",
  no_alternative_available: "ни один из вариантов недоступен",
  requirements_unknown: "требования не заполнены",
  availability_unknown: "наличие оборудования не подтверждено",
};

export const REQUIREMENT_LABELS: Record<string, string> = {
  required: "обязательно",
  optional: "желательно",
  alternative: "одно из",
};

export const SUBSTITUTION_LABELS: Record<string, string> = {
  exact: "полная замена",
  similar: "похожее движение",
  partial: "частичная замена",
};

export const CONFIDENCE_LABELS: Record<string, string> = {
  confirmed: "подтверждено",
  inferred: "выведено",
  unknown: "неизвестно",
};

export const KNOWLEDGE_SOURCE_LABELS: Record<string, string> = {
  seed: "начальный словарь",
  catalog_import: "импорт каталога",
  name_inference: "вывод по названию",
  admin: "администратор",
  questionnaire: "анкета",
  photo: "фотография",
  derived: "вычислено",
};

export const EQUIPMENT_CATEGORY_LABELS: Record<string, string> = {
  free_weight: "свободный вес",
  bench: "скамьи",
  rack: "рамы и стойки",
  machine: "тренажёры",
  cable: "блочные тренажёры",
  cardio: "кардио",
  band: "резина",
  ball: "мячи",
  accessory: "аксессуары",
  support: "опоры",
  bodyweight: "собственный вес",
  bodyweight_support: "опоры для собственного веса",
  strongman: "стронгмен",
  recovery: "восстановление",
};

export const UNMAPPED_REASON_LABELS: Record<string, string> = {
  ambiguous: "требует уточнения",
  unmapped: "нет в словаре",
};

// --- Внешние источники знаний об упражнениях ----------------------------------
//
// Подписи называют решение так, как оно принято: «уже есть» и «вариант
// существующего» — разные утверждения, и в интерфейсе они не должны сливаться в
// «дубль».

export const INGESTION_SOURCE_KIND_LABELS: Record<string, string> = {
  exercise_catalog: "каталог упражнений",
  program_dataset: "датасет программ",
};

export const INGESTION_DECISION_LABELS: Record<string, string> = {
  existing: "уже есть",
  enrichable: "дополняет существующее",
  new_relevant: "новое упражнение",
  duplicate_variant: "повтор внутри источника",
  low_quality: "недостаточно данных",
  questionable: "требует проверки",
  unknown: "решение за человеком",
};

export const INGESTION_STATUS_LABELS: Record<string, string> = {
  pending: "ожидает решения",
  imported: "добавлено",
  enriched: "дополнено",
  skipped: "пропущено",
  rejected: "отклонено",
};

export const QUALITY_STATUS_LABELS: Record<string, string> = {
  ready: "пригодно",
  review: "на проверку",
  reject: "непригодно",
};

/** Тон решения по внешней записи. */
export function ingestionDecisionTone(
  decision: string,
): "ok" | "info" | "warn" | "bad" | "neutral" {
  if (decision === "new_relevant") return "ok";
  if (decision === "enrichable") return "info";
  if (decision === "existing" || decision === "duplicate_variant") return "neutral";
  if (decision === "low_quality") return "bad";
  return "warn";
}

export function qualityStatusTone(status: string): "ok" | "warn" | "bad" | "neutral" {
  if (status === "ready") return "ok";
  if (status === "review") return "warn";
  if (status === "reject") return "bad";
  return "neutral";
}

// Причины решения приходят кодами: они одинаковы в отчёте этапа, в базе и в
// интерфейсе, поэтому расшифровка живёт в одном месте.
export const INGESTION_REASON_LABELS: Record<string, string> = {
  existing_source_link: "связь предыдущего импорта",
  normalized_name_match: "совпало название",
  alias_match: "совпал синоним",
  transliteration_match: "совпало русское название",
  movement_core_match: "совпало движение",
  variant_tokens_match: "совпали признаки выполнения",
  variant_tokens_differ: "различаются признаки выполнения",
  equipment_match: "совпало оборудование",
  equipment_differs: "различается оборудование",
  equipment_unknown: "оборудование неизвестно",
  target_match: "совпала целевая мышца",
  target_in_secondary_muscles: "целевая мышца есть в дополнительных",
  target_differs: "различается целевая мышца",
  target_unknown: "целевая мышца неизвестна",
  secondary_muscles_match: "совпали дополнительные мышцы",
  force_mechanic_match: "совпал характер усилия",
  intra_source_duplicate: "повтор внутри источника",
  technique_present: "есть техника",
  technique_missing: "нет техники",
  technique_too_short: "техника выглядит обрезанной",
  technique_ru_present: "есть русская техника",
  technique_ru_missing: "нет русской техники",
  equipment_mapped: "оборудование сопоставлено",
  equipment_unmapped: "оборудование не сопоставлено",
  equipment_missing: "оборудование не указано",
  target_muscle_mapped: "целевая мышца сопоставлена",
  target_muscle_unmapped: "целевая мышца не сопоставлена",
  secondary_muscles_mapped: "дополнительные мышцы сопоставлены",
  media_present: "есть медиа",
  media_missing: "нет медиа",
  description_present: "есть описание",
  name_encoding_broken: "испорченная кодировка названия",
  name_too_short: "слишком короткое название",
  muscle_terms_ambiguous: "неоднозначные обозначения мышц",
  filled_missing_value: "заполнено пустое поле",
  more_complete_than_canonical: "полнее, чем в справочнике",
  new_exercise_from_source: "поле пришло из источника",
  external_name_kept_as_alias: "название сохранено синонимом",
  program_dataset_observation: "наблюдение датасета программ",
};

export function ingestionReasonLabel(reason: string): string {
  return INGESTION_REASON_LABELS[reason] ?? reason;
}

export const PROVENANCE_FIELD_LABELS: Record<string, string> = {
  name: "название",
  name_ru: "русское название",
  aliases: "синонимы",
  description: "описание",
  technique: "техника",
  technique_ru: "русская техника",
  primary_muscles: "основные мышцы",
  secondary_muscles: "дополнительные мышцы",
  media: "медиа",
  equipment_requirements: "требования к оборудованию",
};

export const SOURCE_RELATION_LABELS: Record<string, string> = {
  origin: "источник упражнения",
  enrichment: "дополнил данные",
  duplicate_variant: "повтор источника",
  observation: "программное наблюдение",
};

/** Тон статуса совместимости. `unknown` — предупреждение, а не отказ. */
export function compatibilityTone(
  status: string,
): "ok" | "warn" | "bad" | "neutral" {
  if (status === "compatible") return "ok";
  if (status === "incompatible") return "bad";
  if (status === "unknown") return "warn";
  return "neutral";
}

export function substitutionTone(value: string): "ok" | "info" | "neutral" {
  if (value === "exact") return "ok";
  if (value === "similar") return "info";
  return "neutral";
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
  ai_rate_limited: "сервис ИИ ограничил число запросов",
  ai_connection_failed: "не удалось соединиться с ИИ",
  ai_runtime_failure: "сбой при обращении к ИИ",
  ai_invalid_response: "ИИ вернул некорректный ответ",
  ai_validation_failed: "ответ ИИ не прошёл проверку",
};

export function aiFallbackReasonLabel(reason: string): string {
  return AI_FALLBACK_REASON_LABELS[reason] ?? reason;
}

// --- Исход попытки одной модели -------------------------------------------------

export const AI_ATTEMPT_OUTCOME_LABELS: Record<string, string> = {
  success: "программа принята",
  invalid_output: "ответ не прошёл проверку",
  provider_error: "сервис не ответил",
  budget_exhausted: "время вышло",
  // Модель отсеяна проверкой готовности: полный запрос к ней не отправлялся.
  // Отличать от «сервис не ответил» важно — это разные причины и разная цена.
  probe_failed: "не прошла проверку готовности",
};

export function aiAttemptOutcomeLabel(outcome: string): string {
  return AI_ATTEMPT_OUTCOME_LABELS[outcome] ?? outcome;
}

export function aiAttemptOutcomeTone(
  outcome: string
): "ok" | "warn" | "bad" | "neutral" {
  if (outcome === "success") return "ok";
  if (outcome === "invalid_output") return "warn";
  if (outcome === "provider_error" || outcome === "probe_failed") return "bad";
  return "neutral";
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
  ai_prompt_updated: "инструкция изменена",
  ai_prompt_deleted: "инструкция удалена",
  ai_generation_fallback: "программа собрана без ИИ",
  ai_model_attempts: "попытки моделей при сборке программы",
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

// --- Аналитика генерации -------------------------------------------------------
//
// Единица анализа — операция генерации: попытка построить программу. Программа
// существует только при успехе, поэтому «генераций» и «программ» — разные числа,
// и подписи не должны их смешивать.

export const GENERATION_STATUS_LABELS: Record<string, string> = {
  pending: "ожидает",
  running: "выполняется",
  succeeded: "успешно",
  failed: "ошибка",
};

export function generationStatusLabel(status: string): string {
  return GENERATION_STATUS_LABELS[status] ?? status;
}

export function generationStatusTone(
  status: string,
): "ok" | "warn" | "bad" | "neutral" {
  if (status === "succeeded") return "ok";
  if (status === "running" || status === "pending") return "warn";
  if (status === "failed") return "bad";
  return "neutral";
}

export const GENERATION_TRIGGER_LABELS: Record<string, string> = {
  auto_finalization: "после подтверждения анкеты",
  admin_request: "запрос администратора",
};

export function generationTriggerLabel(trigger: string): string {
  return GENERATION_TRIGGER_LABELS[trigger] ?? trigger;
}

export const GENERATION_ERROR_LABELS: Record<string, string> = {
  profile_not_found: "анкета не найдена",
  validation_failed: "результат не прошёл проверку",
  ai_not_configured: "ИИ не настроен",
  ai_unsupported_protocol: "способ подключения не поддерживается",
  ai_timeout: "ИИ не ответил вовремя",
  ai_connection_failed: "не удалось соединиться с ИИ",
  ai_rate_limited: "сервис ИИ ограничил число запросов",
  ai_invalid_response: "ИИ вернул некорректный ответ",
  ai_runtime_failure: "сбой при обращении к ИИ",
  generation_failed: "не удалось собрать программу",
  persistence_failed: "не удалось сохранить программу",
  unexpected_error: "непредвиденная ошибка",
};

export function generationErrorLabel(code: string): string {
  return GENERATION_ERROR_LABELS[code] ?? code;
}

export const VALIDATION_STATE_LABELS: Record<string, string> = {
  valid: "принято сразу",
  repaired: "принято после исправления",
  failed: "не прошло проверку",
};

export function validationStateLabel(state: string): string {
  return VALIDATION_STATE_LABELS[state] ?? state;
}

/** Названия показателей в сравнении версий инструкции. */
export const METRIC_LABELS: Record<string, string> = {
  success_rate: "Доля успешных генераций",
  failure_rate: "Доля отказов",
  validation_failure_rate: "Доля непройденных проверок",
  fallback_rate: "Доля сборок без ИИ",
  avg_duration_ms: "Среднее время генерации",
  avg_latency_ms: "Средний ответ модели",
};

export function metricLabel(metric: string): string {
  return METRIC_LABELS[metric] ?? metric;
}

export const TIME_BUCKET_LABELS: Record<string, string> = {
  hour: "по часам",
  day: "по дням",
};

// --- Форматирование значений ----------------------------------------------------

/**
 * Доля в процентах. `null` — это не ноль: значение показывается как «—»,
 * потому что 0% означало бы «отказов не было», а не «считать не на чем».
 */
export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1).replace(/\.0$/, "")}%`;
}

/** Целое число или «—», если значения нет. */
export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("ru-RU");
}

/** Длительность из миллисекунд в человекочитаемый вид. */
export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${ms} мс`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} с`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return rest ? `${minutes} мин ${rest} с` : `${minutes} мин`;
}

/** Дата и время в локальном формате; «—», если значения нет. */
export function dateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Только дата: для подписей осей графика, где время не нужно. */
export function shortDate(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit" });
}

export function shortDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
  });
}

// --- Инфраструктура: компоненты ---------------------------------------------------

export const COMPONENT_TYPE_LABELS: Record<string, string> = {
  backend: "Сервер",
  admin_web: "Веб-интерфейс",
  telegram_gateway: "Шлюз Telegram",
  worker: "Фоновый обработчик",
  max_gateway: "Шлюз MAX",
  smtp_connector: "Почтовый коннектор",
};

export function componentTypeLabel(value: string): string {
  return COMPONENT_TYPE_LABELS[value] ?? value;
}

export const CAPABILITY_LABELS: Record<string, string> = {
  telegram_polling: "приём сообщений Telegram",
  telegram_delivery: "отправка в Telegram",
};

export function capabilityLabel(value: string): string {
  return CAPABILITY_LABELS[value] ?? value;
}

export const COMPONENT_STATE_LABELS: Record<string, string> = {
  unknown: "не проверено",
  healthy: "работает",
  compatible: "совместим",
  update_recommended: "есть обновление",
  update_required: "требуется обновление",
  incompatible: "несовместим",
  offline: "не отвечает",
};

export function componentStateLabel(value: string): string {
  return COMPONENT_STATE_LABELS[value] ?? value;
}

/**
 * Тон состояния компонента. `update_required` и `incompatible` — красные:
 * это отказ работы, а не предупреждение. `offline` — тоже красный: компонент
 * не выполняет свою функцию.
 */
export function componentStateTone(
  value: string
): "ok" | "warn" | "bad" | "neutral" {
  if (value === "compatible" || value === "healthy") return "ok";
  if (value === "update_recommended") return "warn";
  if (value === "update_required" || value === "incompatible" || value === "offline")
    return "bad";
  return "neutral";
}
