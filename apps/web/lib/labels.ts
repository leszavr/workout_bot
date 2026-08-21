// Русскоязычные подписи для значений enum, приходящих из API.
// Все надписи интерфейса — только на русском.

export const STATUS_LABELS: Record<string, string> = {
  draft: "черновик",
  confirmed: "подтверждён",
  in_progress: "в работе",
  generated: "сгенерирована",
  validated: "проверена",
  active: "активна",
  archived: "в архиве",
  failed: "ошибка",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

export const GENERATION_SOURCE_LABELS: Record<string, string> = {
  deterministic: "детерминированная",
  ai: "AI",
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

// --- AI-конфигурация -------------------------------------------------------------

export const AI_TASK_LABELS: Record<string, string> = {
  workout_generation: "Генерация программы тренировок",
  program_adjustment: "Корректировка программы",
  profile_analysis: "Анализ профиля",
  exercise_explanation: "Объяснение упражнений",
  user_chat: "Чат с пользователем",
  feedback_analysis: "Анализ обратной связи",
};

export function aiTaskLabel(taskType: string): string {
  return AI_TASK_LABELS[taskType] ?? taskType;
}

export const AI_PROTOCOL_LABELS: Record<string, string> = {
  openai_compatible: "OpenAI-совместимый",
  anthropic: "Anthropic",
  custom: "Произвольный",
};

export function aiProtocolLabel(protocol: string): string {
  return AI_PROTOCOL_LABELS[protocol] ?? protocol;
}

// --- Готовность AI-конфигурации ---------------------------------------------------

export const AI_READINESS_ICONS: Record<string, string> = {
  ok: "✓",
  warning: "!",
  missing: "○",
  failed: "✗",
};

export function aiReadinessIcon(status: string): string {
  return AI_READINESS_ICONS[status] ?? "○";
}

export const AI_READINESS_STATUS_LABELS: Record<string, string> = {
  ok: "готово",
  warning: "предупреждение",
  missing: "не настроено",
  failed: "ошибка",
};

export function aiReadinessStatusLabel(status: string): string {
  return AI_READINESS_STATUS_LABELS[status] ?? status;
}

export const AI_AUDIT_EVENT_LABELS: Record<string, string> = {
  ai_provider_created: "провайдер создан",
  ai_provider_updated: "провайдер изменён",
  ai_provider_deleted: "провайдер удалён",
  ai_endpoint_created: "эндпоинт создан",
  ai_endpoint_updated: "эндпоинт изменён",
  ai_endpoint_deleted: "эндпоинт удалён",
  ai_endpoint_secret_rotated: "ключ эндпоинта обновлён",
  ai_model_created: "модель создана",
  ai_model_updated: "модель изменена",
  ai_model_deleted: "модель удалена",
  ai_task_updated: "конфигурация задачи сохранена",
  ai_prompt_created: "версия промпта создана",
};

export function aiAuditEventLabel(eventType: string): string {
  return AI_AUDIT_EVENT_LABELS[eventType] ?? eventType;
}

export const AI_USAGE_STATUS_LABELS: Record<string, string> = {
  success: "успех",
  error: "ошибка",
};

export function aiUsageStatusLabel(status: string): string {
  return AI_USAGE_STATUS_LABELS[status] ?? status;
}

export const GENERATOR_LABELS: Record<string, string> = {
  ai: "AI",
  deterministic: "детерминированный",
};

export function generatorLabel(generator: string): string {
  return GENERATOR_LABELS[generator] ?? generator;
}
