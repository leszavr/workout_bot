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
