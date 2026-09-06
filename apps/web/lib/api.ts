// API-клиент внутреннего интерфейса. Хранит JWT в localStorage,
// добавляет заголовок Authorization ко всем запросам.

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

const TOKEN_KEY = "workout_admin_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  // Заполняется для 409 при удалении: что именно блокирует операцию.
  blockers?: AIDeleteBlocker[];
  constructor(status: number, message: string, blockers?: AIDeleteBlocker[]) {
    super(message);
    this.status = status;
    this.blockers = blockers;
  }
}

// FastAPI отдаёт ошибку в поле detail: строкой либо объектом с блокерами.
// Без разбора UI показал бы пользователю сырой JSON.
function parseErrorBody(status: number, body: string): ApiError {
  if (!body) return new ApiError(status, `Ошибка ${status}`);
  try {
    const parsed = JSON.parse(body) as {
      detail?: string | { message?: string; blockers?: AIDeleteBlocker[] };
    };
    const detail = parsed.detail;
    if (typeof detail === "string") return new ApiError(status, detail);
    if (detail && typeof detail === "object") {
      return new ApiError(
        status,
        detail.message || `Ошибка ${status}`,
        detail.blockers
      );
    }
  } catch {
    // Не JSON — показываем тело как есть.
  }
  return new ApiError(status, body);
}

// Предел ожидания ответа. Без него сборка программы через ИИ выглядит как
// зависший интерфейс: кнопка крутится, пока сервер перебирает попытки.
const DEFAULT_TIMEOUT_MS = 30_000;
// Сборка программы идёт дольше обычного запроса: ИИ пишет ответ минуты.
const LONG_TIMEOUT_MS = 300_000;

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs: number = DEFAULT_TIMEOUT_MS
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (e) {
    if (controller.signal.aborted) {
      throw new ApiError(
        408,
        `Сервер не ответил за ${Math.round(timeoutMs / 1000)} с. Повторите попытку.`
      );
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Требуется авторизация");
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    const error = parseErrorBody(response.status, detail);
    // Пароль выдан администратором как временный: до его смены API закрыт.
    // Обрабатывается здесь, чтобы каждая страница не повторяла эту логику.
    if (
      response.status === 403 &&
      error.message === PASSWORD_CHANGE_REQUIRED &&
      typeof window !== "undefined" &&
      !window.location.pathname.startsWith("/change-password")
    ) {
      window.location.href = "/change-password";
    }
    throw error;
  }
  // 204 No Content: тела нет, парсить нечего.
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

// Загрузка бинарного/HTML-ответа. Обычная ссылка здесь не работает: токен
// лежит в localStorage, а не в cookie, поэтому браузер не отправил бы его при
// переходе, и вместо документа открылась бы страница логина.
async function requestBlob(
  path: string,
  timeoutMs: number = LONG_TIMEOUT_MS
): Promise<Blob> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers,
      cache: "no-store",
      signal: controller.signal,
    });
  } catch (e) {
    if (controller.signal.aborted) {
      throw new ApiError(
        408,
        `Сервер не ответил за ${Math.round(timeoutMs / 1000)} с. Повторите попытку.`
      );
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }

  if (response.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Требуется авторизация");
  }
  if (!response.ok) {
    throw parseErrorBody(response.status, await response.text().catch(() => ""));
  }
  return response.blob();
}

// --- Авторизация и текущий пользователь --------------------------------------------

// Значение detail, которым backend сообщает «пароль нужно сменить».
export const PASSWORD_CHANGE_REQUIRED = "password_change_required";

export interface LoginResult {
  role: string;
  must_change_password: boolean;
}

export interface CurrentUser {
  login: string;
  role: string;
  display_name: string | null;
  must_change_password: boolean;
  // Вход выполнен аварийным администратором из переменных окружения.
  is_env_admin: boolean;
  can_write: boolean;
}

export interface AdminUserItem {
  id: number;
  login: string;
  display_name: string | null;
  role: string;
  is_active: boolean;
  must_change_password: boolean;
  has_password: boolean;
  last_login_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface PasswordResetResult {
  login: string;
  temporary_password: string;
  must_change_password: boolean;
}

export async function login(
  loginName: string,
  password: string
): Promise<LoginResult> {
  const body = await request<{
    access_token: string;
    role: string;
    must_change_password: boolean;
  }>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ login: loginName, password }),
  });
  setToken(body.access_token);
  return { role: body.role, must_change_password: body.must_change_password };
}

export const authApi = {
  me: () => request<CurrentUser>("/api/v1/auth/me"),
  changePassword: async (currentPassword: string, newPassword: string) => {
    const body = await request<{ access_token: string; role: string }>(
      "/api/v1/auth/change-password",
      {
        method: "POST",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      }
    );
    // Новый токен уже без флага обязательной смены пароля.
    setToken(body.access_token);
  },
};

export const usersApi = {
  list: () => request<ListResponse<AdminUserItem>>("/api/v1/admin/users"),
  create: (body: Record<string, unknown>) =>
    request<AdminUserItem>("/api/v1/admin/users", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patch: (id: number, body: Record<string, unknown>) =>
    request<AdminUserItem>(`/api/v1/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  remove: (id: number) =>
    request<void>(`/api/v1/admin/users/${id}`, { method: "DELETE" }),
  resetPassword: (id: number) =>
    request<PasswordResetResult>(`/api/v1/admin/users/${id}/reset-password`, {
      method: "POST",
    }),
};

export interface Dashboard {
  users_total: number;
  profiles_total: number;
  profiles_today: number;
  exercises_total: number;
  programs_total: number | null;
}

export interface ProfileListItem {
  profile_id: string;
  display_number: string | null;
  name: string | null;
  age: number | null;
  primary_goal: string | null;
  status: string;
  created_at: string | null;
  // Маркеры исполнения анкеты: собрана ли программа и ушла ли она человеку.
  // Вычисляются на сервере по фактическому составу программ и доставок.
  has_program: boolean;
  delivered: boolean;
  delivered_at: string | null;
  delivery_status: string | null;
}

// Способы сортировки списка анкет. Сервер принимает только эти значения.
export type ProfileSort =
  | "created_desc"
  | "created_asc"
  | "generated_first"
  | "not_generated_first"
  | "delivered_first"
  | "not_delivered_first";

export interface ProfileListResponse extends ListResponse<ProfileListItem> {
  sort: ProfileSort;
}

export interface ProfileDetail {
  profile_id: string;
  display_number: string | null;
  status: string;
  profile_version: number;
  created_at: string | null;
  updated_at: string | null;
  data: Record<string, unknown>;
  consents: Array<{
    consent_type: string;
    consent_version: string;
    granted: boolean;
    granted_at: string | null;
    source: string;
  }>;
}

export interface ExerciseListItem {
  id: number;
  external_id: string;
  name: string;
  name_ru: string | null;
  equipment: string[];
  primary_muscles: string[];
  secondary_muscles: string[];
  difficulty: string | null;
  exercise_type: string | null;
  force: string | null;
  mechanic: string | null;
  source: string;
  is_active: boolean;
  has_media: boolean;
  /**
   * Результат детерминированной проверки совместимости. Приходит только когда
   * в запросе указано доступное оборудование: без него статус не вычисляется, и
   * `null` означает «не проверялось», а не «неизвестно».
   */
  compatibility?: ExerciseCompatibility | null;
}

/** Статус совместимости упражнения с доступным оборудованием. */
export type CompatibilityStatus = "compatible" | "incompatible" | "unknown";

export interface ExerciseCompatibility {
  status: CompatibilityStatus;
  reason: string;
  missing: string[];
  matched: string[];
  unknown: string[];
}

/** Число упражнений по одному значению признака в текущей выборке. */
export interface FacetCount {
  value: string;
  count: number;
}

export interface ExerciseFacets {
  exercise_types: FacetCount[];
  difficulties: FacetCount[];
  equipment: FacetCount[];
  primary_muscles: FacetCount[];
  forces: FacetCount[];
  mechanics: FacetCount[];
}

export type ExerciseSort =
  | "name"
  | "name_ru"
  | "exercise_type"
  | "difficulty"
  | "force"
  | "mechanic"
  | "created_at";

export type ActiveFilter = "active" | "inactive" | "all";
export type MediaFilter = "with" | "without" | "all";
/** Состояние знания о требованиях упражнения к оборудованию. */
export type EquipmentKnowledgeFilter = "known" | "unknown" | "all";
export type RequirementKindFilter =
  | "required"
  | "optional"
  | "alternative"
  | "any";

/** Фильтр каталога. Списки внутри поля — OR, разные поля — AND. */
export interface ExerciseQueryParams {
  search?: string;
  exercise_type?: string[];
  difficulty?: string[];
  equipment?: string[];
  primary_muscle?: string[];
  force?: string[];
  mechanic?: string[];
  /**
   * Canonical ID словаря оборудования. Отличается от `equipment`: там значения
   * источника каталога (`barbell`, `machine`), здесь записи базы знаний
   * (`cable_machine`, `leg_press`).
   */
  equipment_id?: string[];
  capability?: string[];
  requirement_kind?: RequirementKindFilter;
  equipment_knowledge?: EquipmentKnowledgeFilter;
  /** Оборудование «на руках»: по нему считается статус совместимости. */
  available_equipment?: string[];
  /** Считать ли неперечисленное оборудование отсутствующим, а не неизвестным. */
  assume_unlisted_unavailable?: boolean;
  compatibility?: CompatibilityStatus[];
  is_active?: ActiveFilter;
  media?: MediaFilter;
  sort_by?: ExerciseSort;
  order?: SortOrder;
  limit?: number;
  offset?: number;
  with_facets?: boolean;
}

export interface ExerciseListResponse extends PagedResponse<ExerciseListItem> {
  /** Приходит только при `with_facets=true`. */
  facets?: ExerciseFacets;
  /**
   * Сколько строк страницы прошло фильтр по статусу совместимости. Приходит
   * только при таком фильтре: `total` относится к выборке до его применения,
   * потому что статус вычисляется для показанной страницы, а не для каталога.
   */
  filtered_page_count?: number;
}

export interface ExerciseMediaItem {
  sequence: number;
  /** `image` — статичный кадр, `animation` — анимация выполнения. */
  media_type: string;
  mime_type: string;
  width: number;
  height: number;
  size_bytes: number;
  source: string | null;
  license: string | null;
  url: string;
}

export interface ExerciseDetail extends ExerciseListItem {
  source_version: string | null;
  aliases: string[];
  description: string | null;
  technique: string | null;
  technique_ru: string | null;
  common_mistakes: string | null;
  contraindications: string[];
  limitations: string[];
  images: string[];
  media?: ExerciseMediaItem[];
}

export interface ListResponse<T> {
  total: number;
  items: T[];
}

/** Список со страницей: `total` относится ко всей выборке, не к странице. */
export interface PagedResponse<T> extends ListResponse<T> {
  limit: number;
  offset: number;
}

export interface ProgramExercise {
  exercise_external_id: string;
  exercise_source: string;
  order: number;
  sets: number;
  repetitions_min: number;
  repetitions_max: number;
  rest_seconds: number;
  intensity: string | null;
  notes: string | null;
  technique_notes: string | null;
}

export interface TrainingDay {
  day_number: number;
  title: string;
  focus: string;
  exercises: ProgramExercise[];
}

export interface ProgramDetail {
  schema_version: string;
  program_id: string | null;
  profile_id: string;
  version: number;
  status: string;
  generation: {
    source: string;
    generator_version: string;
    safe_pool_size: number | null;
    candidate_pool_total: number | null;
    provider: string | null;
    model: string | null;
    prompt_version: number | null;
  };
  title: string;
  description: string | null;
  duration_weeks: number;
  training_days_per_week: number;
  training_days: TrainingDay[];
  progression: {
    description: string | null;
    weekly_increase_percent: number | null;
  };
  safety_notes: string[];
  created_at: string | null;
  updated_at: string | null;
}

export interface ProgramListItem {
  program_id: string;
  profile_id: string;
  version: number;
  status: string;
  title: string;
  generation_source: string;
  generator_version: string;
  training_days_per_week: number;
  duration_weeks: number;
  created_at: string | null;
  // Отправлена ли программа пользователю в Telegram.
  delivered?: boolean;
}

export interface ProgramVersionInfo {
  version: number;
  status: string;
  created_at: string | null;
}

export interface ProgramResponse {
  program: ProgramDetail;
  versions: ProgramVersionInfo[];
}

export interface GenerateResponse {
  program: ProgramDetail;
  // Operational-состояние генерации (Phase 1.2-B): повторный запрос той же
  // логической генерации не создаёт вторую программу.
  // Phase 1.2-C: генерация идёт через единый оркестратор, поэтому в ответе
  // видна фактически применённая стратегия.
  //
  // `status` не nullable: успешный ответ всегда означает завершённую
  // генерацию. Остальные operational-поля nullable, потому что job-контур
  // опционален на уровне сервера.
  generation: {
    reused_existing: boolean;
    job_id: string | null;
    status: string;
    attempts: number | null;
    last_error_code: string | null;
    requested_generator: string | null;
    actual_generator: string | null;
    fallback_used: boolean;
    fallback_reason_code: string | null;
  };
  pool_stats: {
    total_exercises: number;
    candidates_included: number;
    candidates_excluded: number;
    safe_allowed: number;
    safe_excluded: number;
    safe_warnings: number;
    safe_requires_review: number;
    active_restrictions: string[];
  };
}

// --- AI Configuration (этап 3B) -------------------------------------------------

export interface AIProviderItem {
  id: number;
  name: string;
  slug: string;
  protocol: string;
  enabled: boolean;
  priority: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface AIEndpointItem {
  id: number;
  provider_id: number;
  name: string;
  base_url: string;
  timeout_seconds: number;
  max_retries: number;
  enabled: boolean;
  priority: number;
  has_api_key: boolean;
  masked_api_key: string | null;
  last_test_at: string | null;
  last_test_status: string | null;
  last_test_error_type: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AIModelItem {
  id: number;
  endpoint_id: number;
  model_id: string;
  display_name: string;
  enabled: boolean;
  priority: number;
  context_window: number | null;
  max_output_tokens: number | null;
  supports_structured_output: boolean;
  supports_json_schema: boolean;
  supports_streaming: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface AIDiscoveredModel {
  model_id: string;
  display_name: string;
  owned_by: string | null;
  already_added: boolean;
}

export interface AITaskBinding {
  id: number;
  task_config_id: number;
  model_id: number;
  priority: number;
  is_primary: boolean;
}

export interface AITaskItem {
  id: number | null;
  task_type: string;
  enabled: boolean;
  temperature: number;
  max_tokens: number | null;
  timeout_seconds: number;
  prompt_version: number | null;
  bindings: AITaskBinding[];
  created_at: string | null;
  updated_at: string | null;
}

export interface AIEndpointTestResult {
  success: boolean;
  latency_ms: number;
  provider: string;
  endpoint: string;
  // null, когда проверять нечего: все модели подключения выключены.
  model: string | null;
  message?: string;
  error_type?: string;
}

// --- Готовность AI-конфигурации (Phase 1.1) --------------------------------------

export interface AIReadinessCheck {
  key: string;
  title: string;
  status: string;
  detail: string;
  action: string | null;
  blocking: boolean;
  reason_code: string | null;
}

export interface AIReadinessChainEntry {
  priority: number;
  is_primary: boolean;
  provider: string;
  endpoint: string;
  model_id: string;
  model_display_name: string;
  model_pk: number | null;
}

export interface AIReadinessReport {
  task_type: string;
  ready: boolean;
  checks: AIReadinessCheck[];
  chain: AIReadinessChainEntry[];
  generation: {
    primary_generator: string;
    fallback_generator: string;
    auto_generate_after_finalize: boolean;
    ai_in_strategy: boolean;
  };
}

export interface AIUsageItem {
  id: number;
  task_type: string;
  provider_id: number | null;
  endpoint_id: number | null;
  model_id: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number | null;
  status: string;
  error_type: string | null;
  created_at: string | null;
}

export interface AIAuditItem {
  id: number;
  event_type: string;
  actor: string | null;
  entity_type: string | null;
  entity_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

// --- Инструкции для ИИ (промпты) -------------------------------------------------
// Список отдаёт превью и метаданные, полный текст — только карточка: инструкция
// бывает в десятки килобайт, и тянуть её целиком в список незачем.

export interface AIPromptItem {
  id: number;
  task_type: string;
  version: number;
  name: string;
  enabled: boolean;
  // Выбрана в настройках задачи: удалять такую нельзя.
  in_use: boolean;
  system_prompt_preview: string;
  system_prompt_length: number;
  user_template_length: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface AIPromptDetail {
  id: number;
  task_type: string;
  version: number;
  name: string;
  system_prompt: string;
  user_template: string;
  enabled: boolean;
  in_use: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface AIPromptListResponse extends ListResponse<AIPromptItem> {
  next_version: number;
  active_version: number | null;
}

// --- Попытки моделей внутри одной генерации ---------------------------------------

export interface AIModelAttempt {
  priority: number;
  is_primary: boolean;
  provider: string;
  model_id: string;
  model_pk: number | null;
  initial_valid: boolean;
  repair_attempts: number;
  outcome: string;
  error_type: string | null;
  detail: string | null;
}

export interface AIModelAttemptsItem {
  id: number;
  event_type: string;
  created_at: string | null;
  metadata: {
    task_type?: string;
    models_tried?: number;
    attempts?: AIModelAttempt[];
  };
}

// --- Infrastructure health (Phase 1.1.1) -----------------------------------------
// Дерево строится backend'ом из реальной конфигурации. Frontend НЕ хранит
// собственный список провайдеров и моделей и ничего не выводит сам.

export interface AIHealthTaskUsage {
  task_type: string;
  task_enabled: boolean;
  is_primary: boolean;
  priority: number;
}

export interface AIHealthModel {
  id: number | null;
  model_id: string;
  display_name: string;
  enabled: boolean;
  availability: string;
  reason: string | null;
  in_active_use: boolean;
  tasks: AIHealthTaskUsage[];
}

export interface AIHealthEndpoint {
  id: number | null;
  name: string;
  base_url: string;
  enabled: boolean;
  has_api_key: boolean;
  health: string;
  reason: string | null;
  last_checked_at: string | null;
  last_check_status: string | null;
  last_check_error_type: string | null;
  last_call_at: string | null;
  last_call_status: string | null;
  last_call_error_type: string | null;
  models: AIHealthModel[];
}

export interface AIHealthProvider {
  id: number | null;
  name: string;
  slug: string;
  protocol: string;
  protocol_supported: boolean;
  enabled: boolean;
  health: string;
  reason: string | null;
  endpoints: AIHealthEndpoint[];
}

export interface AIInfrastructureHealth {
  generated_at: string;
  providers: AIHealthProvider[];
  summary: {
    providers_total: number;
    providers_healthy: number;
    endpoints_total: number;
    models_total: number;
    models_available: number;
    models_in_active_use: number;
  };
}

export interface AIFallbackEventItem {
  id: number;
  event_type: string;
  created_at: string | null;
  metadata: {
    requested_generator?: string;
    actual_generator?: string;
    reason_code?: string;
    detail?: string;
    ai_attempted?: boolean;
  };
}

export interface AIDeleteBlocker {
  type: string;
  task_type?: string;
  task_enabled?: boolean;
  model_id?: number;
  prompt_version?: number;
  // Для анкет: сколько программ мешает удалению.
  count?: number;
  detail: string;
}

// --- Аналитика генерации --------------------------------------------------------
//
// Единица анализа — операция генерации, а не программа: программа существует
// только при успехе, и по ней не видно отказов.
//
// Доли объявлены `number | null`. Ноль здесь означал бы «отказов не было», а
// null — «считать не на чем»; сводить их к нулю в интерфейсе нельзя.

export type SortOrder = "asc" | "desc";
export type TimeBucket = "hour" | "day";
export type ValidationState = "valid" | "failed" | "repaired";
export type GeneratorKind = "ai" | "deterministic" | "manual";
export type GenerationResult = "pending" | "running" | "succeeded" | "failed";

export type ModelSort =
  | "usage"
  | "success_rate"
  | "failure_rate"
  | "fallback_rate"
  | "avg_latency_ms"
  | "repair_attempts"
  | "model";

export type PromptSort =
  | "prompt_version"
  | "usage"
  | "success_rate"
  | "failure_rate"
  | "validation_failures"
  | "fallback_rate"
  | "avg_duration_ms"
  | "repair_attempts";

export type GenerationSort = "created_at" | "duration_ms" | "attempts" | "status";

export interface AnalyticsFilter {
  date_from?: string;
  date_to?: string;
  provider?: string;
  model?: string;
  prompt_version?: number;
  generator?: GeneratorKind;
  result?: GenerationResult;
  fallback?: boolean;
  validation?: ValidationState;
  trigger?: string;
}

/** Query-строка аналитики: пустые фильтры не отправляются. */
export function analyticsQuery(
  filter?: AnalyticsFilter,
  extra?: Record<string, string | number | boolean | undefined>
): string {
  const qs = new URLSearchParams();
  const merged: Record<string, unknown> = { ...(filter ?? {}), ...(extra ?? {}) };
  for (const [key, value] of Object.entries(merged)) {
    // Явное false — это фильтр «без подмены генератора», а не его отсутствие,
    // поэтому отбрасываются только undefined, null и пустая строка.
    if (value === undefined || value === null || value === "") continue;
    qs.set(key, String(value));
  }
  const query = qs.toString();
  return query ? `?${query}` : "";
}

export interface AnalyticsGenerationTotals {
  total: number;
  succeeded: number;
  failed: number;
  active: number;
  by_ai: number;
  by_deterministic: number;
  fallback: number;
  deterministic_fallback: number;
  validation_failures: number;
  repaired: number;
  repair_attempts: number;
  job_attempts: number;
  success_rate: number | null;
  failure_rate: number | null;
  fallback_rate: number | null;
  ai_share: number | null;
  avg_duration_ms: number | null;
  p95_duration_ms: number | null;
}

export interface AnalyticsCallTotals {
  total: number;
  succeeded: number;
  failed: number;
  success_rate: number | null;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  total_tokens: number;
}

export interface AnalyticsOverview {
  generations: AnalyticsGenerationTotals;
  calls: AnalyticsCallTotals;
  sample: {
    generations: number;
    confident: boolean;
    min_confident: number;
  };
}

export interface AnalyticsTimeseriesPoint {
  bucket: string | null;
  total: number;
  succeeded: number;
  failed: number;
  by_ai: number;
  fallback: number;
  avg_duration_ms: number | null;
  success_rate: number | null;
}

export interface AnalyticsTimeseriesResponse
  extends ListResponse<AnalyticsTimeseriesPoint> {
  bucket: TimeBucket;
}

export interface AnalyticsModelRow {
  model: string;
  provider: string | null;
  usage: number;
  succeeded: number;
  failed: number;
  invalid_outputs: number;
  provider_errors: number;
  budget_exhausted: number;
  repair_attempts: number;
  initial_valid: number;
  as_primary: number;
  as_fallback: number;
  generation_fallbacks: number;
  success_rate: number | null;
  failure_rate: number | null;
  fallback_rate: number | null;
  first_answer_rate: number | null;
  avg_latency_ms: number | null;
  calls: number;
  confident: boolean;
}

export interface AnalyticsPromptRow {
  prompt_version: number;
  name: string | null;
  enabled: boolean | null;
  usage: number;
  succeeded: number;
  failed: number;
  validation_failures: number;
  fallback: number;
  repaired: number;
  repair_attempts: number;
  avg_duration_ms: number | null;
  success_rate: number | null;
  failure_rate: number | null;
  validation_failure_rate: number | null;
  fallback_rate: number | null;
  first_used_at: string | null;
  last_used_at: string | null;
  confident: boolean;
}

export interface PromptComparisonMetric {
  metric: string;
  left_version: number;
  right_version: number;
  left_value: number | null;
  right_value: number | null;
  difference_pp: number | null;
  /** null — вывода нет: мало данных или разница в пределах погрешности. */
  better_version: number | null;
  confident: boolean;
  note: string;
}

export interface PromptComparisonResponse {
  left: AnalyticsPromptRow | null;
  right: AnalyticsPromptRow | null;
  metrics: PromptComparisonMetric[];
  missing_versions: number[];
}

export interface AnalyticsGenerationRow {
  job_id: string;
  profile_id: string;
  trigger: string;
  requested_generator: string;
  actual_generator: string | null;
  status: GenerationResult;
  attempts: number;
  program_id: string | null;
  program_version: number | null;
  program_title: string | null;
  last_error_code: string | null;
  created_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  fallback_used: boolean | null;
  fallback_reason_code: string | null;
  model: string | null;
  provider: string | null;
  prompt_version: number | null;
  models_tried: number;
  repair_attempts: number;
  invalid_outputs: number;
  repaired: boolean;
}

export interface AnalyticsAttemptDetail {
  priority: number;
  is_primary: boolean;
  provider: string;
  model_id: string;
  model_pk: number | null;
  initial_valid: boolean;
  repair_attempts: number;
  outcome: string;
  error_type: string | null;
  detail: string | null;
}

export interface AnalyticsCallRow {
  id: number;
  status: string;
  error_type: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  created_at: string | null;
  model: string | null;
  provider: string | null;
  endpoint: string | null;
}

export interface AnalyticsGenerationDetail extends AnalyticsGenerationRow {
  started_at: string | null;
  last_error_message: string | null;
  program_status: string | null;
  fallback_reason: string | null;
  attempt_details: AnalyticsAttemptDetail[];
  calls: AnalyticsCallRow[];
}

export interface AnalyticsFilterOptions {
  models: Array<{ model: string; provider: string | null }>;
  providers: string[];
  prompt_versions: number[];
  triggers: string[];
}

export const aiApi = {
  providers: () =>
    request<ListResponse<AIProviderItem>>("/api/v1/admin/ai/providers"),
  createProvider: (body: Record<string, unknown>) =>
    request<AIProviderItem>("/api/v1/admin/ai/providers", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchProvider: (id: number, body: Record<string, unknown>) =>
    request<AIProviderItem>(`/api/v1/admin/ai/providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteProvider: (id: number) =>
    request<void>(`/api/v1/admin/ai/providers/${id}`, { method: "DELETE" }),
  endpoints: (providerId: number) =>
    request<ListResponse<AIEndpointItem>>(
      `/api/v1/admin/ai/providers/${providerId}/endpoints`
    ),
  createEndpoint: (providerId: number, body: Record<string, unknown>) =>
    request<AIEndpointItem>(
      `/api/v1/admin/ai/providers/${providerId}/endpoints`,
      { method: "POST", body: JSON.stringify(body) }
    ),
  patchEndpoint: (id: number, body: Record<string, unknown>) =>
    request<AIEndpointItem>(`/api/v1/admin/ai/endpoints/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteEndpoint: (id: number) =>
    request<void>(`/api/v1/admin/ai/endpoints/${id}`, { method: "DELETE" }),
  setEndpointSecret: (id: number, apiKey: string) =>
    request<{ has_api_key: boolean; masked_api_key: string | null }>(
      `/api/v1/admin/ai/endpoints/${id}/secret`,
      { method: "PUT", body: JSON.stringify({ api_key: apiKey }) }
    ),
  testEndpoint: (id: number) =>
    request<AIEndpointTestResult>(
      `/api/v1/admin/ai/endpoints/${id}/test`,
      { method: "POST" },
      60_000
    ),
  models: (endpointId: number) =>
    request<ListResponse<AIModelItem>>(
      `/api/v1/admin/ai/endpoints/${endpointId}/models`
    ),
  createModel: (endpointId: number, body: Record<string, unknown>) =>
    request<AIModelItem>(`/api/v1/admin/ai/endpoints/${endpointId}/models`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  // Список моделей приходит от самого сервиса: запрос уходит наружу, поэтому
  // ждём дольше обычного.
  availableModels: (endpointId: number) =>
    request<ListResponse<AIDiscoveredModel>>(
      `/api/v1/admin/ai/endpoints/${endpointId}/available-models`,
      undefined,
      60_000
    ),
  addModelsBulk: (endpointId: number, modelIds: string[]) =>
    request<{ added: AIModelItem[]; skipped: string[] }>(
      `/api/v1/admin/ai/endpoints/${endpointId}/models/bulk`,
      { method: "POST", body: JSON.stringify({ model_ids: modelIds }) }
    ),
  // Список моделей по введённым адресу и ключу, до создания подключения.
  probeModels: (baseUrl: string, apiKey?: string) =>
    request<ListResponse<AIDiscoveredModel>>(
      "/api/v1/admin/ai/probe-models",
      {
        method: "POST",
        body: JSON.stringify({ base_url: baseUrl, api_key: apiKey || null }),
      },
      60_000
    ),
  patchModel: (id: number, body: Record<string, unknown>) =>
    request<AIModelItem>(`/api/v1/admin/ai/models/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteModel: (id: number) =>
    request<void>(`/api/v1/admin/ai/models/${id}`, { method: "DELETE" }),
  tasks: () => request<ListResponse<AITaskItem>>("/api/v1/admin/ai/tasks"),
  putTask: (taskType: string, body: Record<string, unknown>) =>
    request<AITaskItem>(`/api/v1/admin/ai/tasks/${taskType}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  readiness: (taskType = "workout_generation") =>
    request<AIReadinessReport>(
      `/api/v1/admin/ai/readiness?task_type=${taskType}`
    ),
  usage: () => request<ListResponse<AIUsageItem>>("/api/v1/admin/ai/usage"),
  audit: () => request<ListResponse<AIAuditItem>>("/api/v1/admin/ai/audit"),
  fallbackEvents: () =>
    request<ListResponse<AIFallbackEventItem>>(
      "/api/v1/admin/ai/fallback-events"
    ),
  modelAttempts: () =>
    request<ListResponse<AIModelAttemptsItem>>(
      "/api/v1/admin/ai/model-attempts"
    ),
  infrastructureHealth: () =>
    request<AIInfrastructureHealth>("/api/v1/admin/ai/infrastructure-health"),
  refreshInfrastructureHealth: () =>
    request<AIInfrastructureHealth>(
      "/api/v1/admin/ai/infrastructure-health/refresh",
      { method: "POST" }
    ),
  prompts: (taskType = "workout_generation") =>
    request<AIPromptListResponse>(`/api/v1/admin/ai/prompts/${taskType}`),
  prompt: (id: number) =>
    request<AIPromptDetail>(`/api/v1/admin/ai/prompts/detail/${id}`),
  createPrompt: (body: Record<string, unknown>) =>
    request<AIPromptDetail>("/api/v1/admin/ai/prompts", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  patchPrompt: (id: number, body: Record<string, unknown>) =>
    request<AIPromptDetail>(`/api/v1/admin/ai/prompts/detail/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deletePrompt: (id: number) =>
    request<void>(`/api/v1/admin/ai/prompts/detail/${id}`, { method: "DELETE" }),

  // --- Аналитика генерации ------------------------------------------------------
  //
  // Все фильтры уходят на сервер: сортировка и фильтрация показанной страницы
  // отвечали бы на вопрос «какая модель худшая» неверно.

  analyticsOverview: (filter?: AnalyticsFilter) =>
    request<AnalyticsOverview>(
      `/api/v1/admin/ai/analytics/overview${analyticsQuery(filter)}`
    ),
  analyticsTimeseries: (bucket: TimeBucket, filter?: AnalyticsFilter) =>
    request<AnalyticsTimeseriesResponse>(
      `/api/v1/admin/ai/analytics/timeseries${analyticsQuery(filter, {
        bucket,
      })}`
    ),
  analyticsModels: (
    filter?: AnalyticsFilter,
    sort?: { sort_by?: ModelSort; order?: SortOrder }
  ) =>
    request<ListResponse<AnalyticsModelRow>>(
      `/api/v1/admin/ai/analytics/models${analyticsQuery(filter, sort)}`
    ),
  analyticsPrompts: (
    filter?: AnalyticsFilter,
    sort?: { sort_by?: PromptSort; order?: SortOrder }
  ) =>
    request<ListResponse<AnalyticsPromptRow>>(
      `/api/v1/admin/ai/analytics/prompts${analyticsQuery(filter, sort)}`
    ),
  analyticsComparePrompts: (
    left: number,
    right: number,
    filter?: AnalyticsFilter
  ) =>
    request<PromptComparisonResponse>(
      `/api/v1/admin/ai/analytics/prompts/compare${analyticsQuery(filter, {
        left,
        right,
      })}`
    ),
  analyticsGenerations: (
    filter?: AnalyticsFilter,
    page?: {
      limit?: number;
      offset?: number;
      sort_by?: GenerationSort;
      order?: SortOrder;
    }
  ) =>
    request<PagedResponse<AnalyticsGenerationRow>>(
      `/api/v1/admin/ai/analytics/generations${analyticsQuery(filter, {
        limit: page?.limit ?? 25,
        offset: page?.offset ?? 0,
        sort_by: page?.sort_by,
        order: page?.order,
      })}`
    ),
  analyticsGeneration: (jobId: string) =>
    request<AnalyticsGenerationDetail>(
      `/api/v1/admin/ai/analytics/generations/${encodeURIComponent(jobId)}`
    ),
  analyticsFilters: () =>
    request<AnalyticsFilterOptions>("/api/v1/admin/ai/analytics/filters"),
};

// --- Инфраструктура: компоненты (Component Registry) -----------------------------
//
// Список приходит с сервера целиком, включая вердикт совместимости: интерфейс
// не сравнивает версии сам. Иначе фронтенд стал бы вторым источником правил
// совместимости и со временем расходился бы с backend.

export type ComponentCompatibilityState =
  | "unknown"
  | "healthy"
  | "compatible"
  | "update_recommended"
  | "update_required"
  | "incompatible"
  | "offline";

export interface ComponentItem {
  component_id: string;
  component_type: string;
  name: string;
  region: string;
  version: string;
  build_sha: string | null;
  contract_version: number;
  supported_contracts: number[];
  capabilities: string[];
  status: string;
  compatibility_state: ComponentCompatibilityState;
  compatibility_detail: string;
  required_contract?: number | null;
  min_version?: string | null;
  last_heartbeat_at: string | null;
  registered_at: string | null;
  // Backend описывает себя сам и в реестре не хранится.
  self_reported: boolean;
}

export interface ComponentRequirementInfo {
  supported_contracts: number[];
  required_contract: number;
  min_version: string;
  recommended_version: string | null;
}

export interface ComponentsResponse extends ListResponse<ComponentItem> {
  backend: {
    version: string;
    build_sha: string | null;
    contract_version: number;
    supported_contracts: number[];
  };
  requirements: Record<string, ComponentRequirementInfo>;
}

export interface DeploymentSafetyVerdict {
  component_id: string;
  component_type: string;
  state: ComponentCompatibilityState;
  contract_version: number | null;
  required_contract: number | null;
  supported_contracts: number[];
  version: string | null;
  min_version: string | null;
  detail: string;
}

export interface DeploymentSafetyReport {
  result: "SAFE" | "BLOCKED";
  generated_at: string;
  backend_version: string;
  backend_contracts: number[];
  blocking: DeploymentSafetyVerdict[];
  verdicts: DeploymentSafetyVerdict[];
}

export const componentsApi = {
  list: () => request<ComponentsResponse>("/api/v1/admin/components"),
  deploymentSafety: () =>
    request<DeploymentSafetyReport>("/api/v1/admin/components/deployment-safety"),
  forget: (componentId: string) =>
    request<void>(`/api/v1/admin/components/${encodeURIComponent(componentId)}`, {
      method: "DELETE",
    }),
};

// --- AI Providers для UI (публичный API) ----------------------------------------

export interface AIProviderForUI {
  provider_id: number;
  slug: string;
  display_name: string;
  type: string;
  enabled: boolean;
  available_models: Array<{
    model_id: string;
    display_name: string;
    endpoint_id: number;
  }>;
}

// --- Gym Knowledge Base: оборудование ------------------------------------------
//
// Словарь оборудования отделён от каталога упражнений: каталог описывает
// упражнения, словарь — мир, в котором они выполняются. Один и тот же
// canonical ID связывает их.

export interface EquipmentAlias {
  alias: string;
  match_mode: "exact" | "stem";
}

export interface EquipmentItem {
  equipment_id: string;
  name: string;
  name_ru: string;
  category: string;
  description: string | null;
  capabilities: string[];
  aliases: EquipmentAlias[];
  /**
   * Родовое оборудование, частным случаем которого является запись:
   * `leg_press` специализирует `resistance_machine`. Требование родового
   * закрывается частным, обратное неверно.
   */
  specializes: string | null;
  manufacturer: string | null;
  model_name: string | null;
  is_active: boolean;
  /** Сколько упражнений ссылается на эту запись. */
  exercise_count?: number;
}

export interface EquipmentCapability {
  capability_id: string;
  name: string;
  name_ru: string;
  description: string | null;
  is_active: boolean;
}

export type RequirementKind = "required" | "optional" | "alternative";
export type KnowledgeConfidence = "confirmed" | "inferred" | "unknown";

export interface ExerciseRequirement {
  equipment_id: string | null;
  capability_id: string | null;
  requirement: RequirementKind;
  alternative_group: number | null;
  confidence: KnowledgeConfidence;
  source: string;
  notes: string | null;
}

export type SubstitutionType = "exact" | "similar" | "partial";

export interface ExerciseAlternative {
  alternative_external_id: string;
  alternative_source: string;
  substitution: SubstitutionType;
  score: number;
  rationale: Record<string, unknown>;
  source: string;
}

export interface KnowledgeHealth {
  exercises_total: number;
  exercises_active: number;
  equipment_known: number;
  equipment_unknown: number;
  equipment_confirmed: number;
  equipment_inferred: number;
  exercises_with_alternatives: number;
  equipment_items_total: number;
  equipment_items_active: number;
  equipment_items_unused: number;
  capabilities_total: number;
  aliases_total: number;
  requirements_total: number;
  alternatives_total: number;
  unmapped_values: number;
  unmapped_exercises: number;
  orphan_equipment_references: number;
  invalid_capability_references: number;
  impossible_requirement_combinations: number;
  duplicate_requirements: number;
  equipment_known_ratio: number;
  unmapped_summary: Array<{ raw_value: string; reason: string; count: number }>;
}

export interface UnmappedValue {
  exercise_external_id: string;
  exercise_source: string;
  raw_value: string;
  reason: "ambiguous" | "unmapped";
  notes: string | null;
}

export interface EquipmentQueryParams {
  search?: string;
  category?: string[];
  capability?: string[];
  is_active?: ActiveFilter;
  usage?: "used" | "unused" | "all";
  limit?: number;
  offset?: number;
}

export interface EquipmentPayload {
  equipment_id: string;
  name: string;
  name_ru: string;
  category: string;
  description?: string | null;
  capabilities: string[];
  aliases: EquipmentAlias[];
  specializes?: string | null;
  manufacturer?: string | null;
  model_name?: string | null;
  is_active: boolean;
}

export const knowledgeApi = {
  equipment: (params?: EquipmentQueryParams) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    for (const [key, values] of [
      ["category", params?.category],
      ["capability", params?.capability],
    ] as const) {
      for (const value of values ?? []) qs.append(key, value);
    }
    if (params?.is_active) qs.set("is_active", params.is_active);
    if (params?.usage) qs.set("usage", params.usage);
    qs.set("limit", String(params?.limit ?? 50));
    qs.set("offset", String(params?.offset ?? 0));
    return request<PagedResponse<EquipmentItem>>(
      `/api/v1/admin/knowledge/equipment?${qs}`
    );
  },
  equipmentItem: (equipmentId: string) =>
    request<EquipmentItem>(
      `/api/v1/admin/knowledge/equipment/${encodeURIComponent(equipmentId)}`
    ),
  categories: () =>
    request<{ items: FacetCount[] }>(
      "/api/v1/admin/knowledge/equipment/categories"
    ),
  capabilities: () =>
    request<ListResponse<EquipmentCapability>>(
      "/api/v1/admin/knowledge/capabilities"
    ),
  createEquipment: (payload: EquipmentPayload) =>
    request<EquipmentItem>("/api/v1/admin/knowledge/equipment", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateEquipment: (
    equipmentId: string,
    payload: Partial<Omit<EquipmentPayload, "equipment_id">>
  ) =>
    request<EquipmentItem>(
      `/api/v1/admin/knowledge/equipment/${encodeURIComponent(equipmentId)}`,
      { method: "PATCH", body: JSON.stringify(payload) }
    ),
  deactivateEquipment: (equipmentId: string) =>
    request<EquipmentItem>(
      `/api/v1/admin/knowledge/equipment/${encodeURIComponent(equipmentId)}/deactivate`,
      { method: "POST" }
    ),
  deleteEquipment: (equipmentId: string) =>
    request<void>(
      `/api/v1/admin/knowledge/equipment/${encodeURIComponent(equipmentId)}`,
      { method: "DELETE" }
    ),
  requirements: (externalId: string, source?: string) => {
    const qs = new URLSearchParams();
    if (source) qs.set("source", source);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ListResponse<ExerciseRequirement>>(
      `/api/v1/admin/knowledge/exercises/${encodeURIComponent(externalId)}/requirements${suffix}`
    );
  },
  alternatives: (externalId: string, source?: string) => {
    const qs = new URLSearchParams();
    if (source) qs.set("source", source);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ListResponse<ExerciseAlternative>>(
      `/api/v1/admin/knowledge/exercises/${encodeURIComponent(externalId)}/alternatives${suffix}`
    );
  },
  health: () => request<KnowledgeHealth>("/api/v1/admin/knowledge/health"),
  unmapped: (params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    qs.set("limit", String(params?.limit ?? 100));
    qs.set("offset", String(params?.offset ?? 0));
    return request<PagedResponse<UnmappedValue>>(
      `/api/v1/admin/knowledge/unmapped?${qs}`
    );
  },
};

// --- Внешние источники знаний об упражнениях ----------------------------------

/** Источник знаний вместе с последней прочитанной версией. */
export interface IngestionSource {
  source_key: string;
  name: string;
  kind: "exercise_catalog" | "program_dataset";
  homepage: string | null;
  data_license: string | null;
  media_license: string | null;
  attribution: string | null;
  notes: string | null;
  is_active: boolean;
  version: string | null;
  content_hash: string | null;
  retrieved_at: string | null;
  record_count: number;
  // Счётчики приходят ключами вида `decision:new_relevant`, `import:imported`,
  // `quality:ready`: набор значений задаётся сервером, и фиксировать его в типе
  // значило бы дублировать перечисление на клиенте.
  counts: Record<string, number>;
}

export interface IngestionRecord {
  source_key: string;
  source_version: string;
  source_record_id: string;
  raw_name: string;
  normalized_name: string;
  quality_score: number;
  quality_status: "ready" | "review" | "reject";
  quality_reasons: string[];
  decision:
    | "existing"
    | "enrichable"
    | "new_relevant"
    | "duplicate_variant"
    | "low_quality"
    | "questionable"
    | "unknown";
  match_confidence: number;
  match_reasons: string[];
  matched_external_id: string | null;
  matched_source: string | null;
  import_status: "pending" | "imported" | "enriched" | "skipped" | "rejected";
  import_note: string | null;
  imported_at: string | null;
  // Приходят только в карточке одной записи.
  record_hash?: string;
  name_key?: string;
  payload?: Record<string, unknown>;
}

export interface ExerciseFieldProvenance {
  field: string;
  source_key: string;
  source_record_id: string;
  source_version: string;
  reason: string | null;
}

export interface ExerciseSourceLinkOut {
  source_key: string;
  source_record_id: string;
  source_version: string;
  relation: "origin" | "enrichment" | "duplicate_variant" | "observation";
  confidence: number;
  reasons: string[];
}

/** Наблюдение датасета программ: статистика чужих программ, не предписание. */
export interface ProgramObservation {
  source_key: string;
  source_version: string;
  program_count: number;
  occurrence_count: number;
  typical_sets_median: number | null;
  typical_sets_min: number | null;
  typical_sets_max: number | null;
  typical_reps_median: number | null;
  typical_reps_min: number | null;
  typical_reps_max: number | null;
  typical_hold_seconds_median: number | null;
  typical_intensity_median: number | null;
  source_goals: Record<string, number>;
  source_levels: Record<string, number>;
  source_equipment_contexts: Record<string, number>;
}

export interface ExerciseProvenance {
  exercise_external_id: string;
  exercise_source: string;
  fields: ExerciseFieldProvenance[];
  sources: ExerciseSourceLinkOut[];
  program_observations: ProgramObservation[];
}

export interface IngestionHealth {
  external_records_total: number;
  source_links_total: number;
  field_provenance_total: number;
  program_observations_total: number;
  exercises_with_source_links: number;
  exercises_with_observations: number;
  records_imported: number;
  records_enriched: number;
  records_review: number;
  records_rejected: number;
  records_duplicate_variant: number;
  by_source: Record<string, Record<string, number>>;
}

export interface IngestionRecordQuery {
  source?: string[];
  decision?: string[];
  quality?: string[];
  status?: string[];
  search?: string;
  min_confidence?: number;
  max_confidence?: number;
  limit?: number;
  offset?: number;
}

/**
 * Строка запроса для списка внешних записей.
 *
 * Вынесена из клиента отдельной функцией, чтобы её можно было проверить тестом:
 * многозначные фильтры (несколько источников, несколько решений) собираются
 * повторением параметра, а не запятыми, и ошибка здесь не видна до открытия
 * страницы — сервер молча вернул бы «всё» вместо отобранного.
 */
export function ingestionRecordsQuery(params?: IngestionRecordQuery): string {
  const qs = new URLSearchParams();
  for (const [key, values] of [
    ["source", params?.source],
    ["decision", params?.decision],
    ["quality", params?.quality],
    ["status", params?.status],
  ] as const) {
    for (const value of values ?? []) qs.append(key, value);
  }
  if (params?.search) qs.set("search", params.search);
  // Ноль — допустимая граница уверенности, поэтому проверяется undefined, а не
  // ложность значения.
  if (params?.min_confidence !== undefined)
    qs.set("min_confidence", String(params.min_confidence));
  if (params?.max_confidence !== undefined)
    qs.set("max_confidence", String(params.max_confidence));
  qs.set("limit", String(params?.limit ?? 50));
  qs.set("offset", String(params?.offset ?? 0));
  return `?${qs}`;
}

export const ingestionApi = {
  sources: () =>
    request<ListResponse<IngestionSource>>("/api/v1/admin/ingestion/sources"),
  records: (params?: IngestionRecordQuery) =>
    request<PagedResponse<IngestionRecord>>(
      `/api/v1/admin/ingestion/records${ingestionRecordsQuery(params)}`
    ),
  record: (sourceKey: string, recordId: string) =>
    request<IngestionRecord>(
      `/api/v1/admin/ingestion/records/${sourceKey}/${encodeURIComponent(recordId)}`
    ),
  provenance: (externalId: string, source?: string) => {
    const qs = new URLSearchParams();
    if (source) qs.set("source", source);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ExerciseProvenance>(
      `/api/v1/admin/ingestion/exercises/${encodeURIComponent(externalId)}/provenance${suffix}`
    );
  },
  health: () => request<IngestionHealth>("/api/v1/admin/ingestion/health"),
};

export const api = {
  dashboard: () => request<Dashboard>("/api/v1/dashboard"),
  profiles: (params?: {
    search?: string;
    status?: string;
    generated?: boolean;
    delivered?: boolean;
    sort?: ProfileSort;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    if (params?.status) qs.set("status", params.status);
    // Явное false — это фильтр «без программы», а не отсутствие фильтра.
    if (params?.generated !== undefined) qs.set("generated", String(params.generated));
    if (params?.delivered !== undefined) qs.set("delivered", String(params.delivered));
    if (params?.sort) qs.set("sort", params.sort);
    qs.set("limit", String(params?.limit ?? 50));
    qs.set("offset", String(params?.offset ?? 0));
    return request<ProfileListResponse>(`/api/v1/profiles?${qs}`);
  },
  profile: (id: string) => request<ProfileDetail>(`/api/v1/profiles/${id}`),
  deleteProfile: (id: string) =>
    request<void>(`/api/v1/profiles/${id}`, { method: "DELETE" }),
  deleteProgram: (id: string) =>
    request<void>(`/api/v1/programs/${id}`, { method: "DELETE" }),
  exercises: (params?: ExerciseQueryParams) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    // Многозначные фильтры уходят повторяющимся параметром: FastAPI собирает
    // их в список, и «штанга или гантели» — это один запрос, а не два.
    for (const [key, values] of [
      ["exercise_type", params?.exercise_type],
      ["difficulty", params?.difficulty],
      ["equipment", params?.equipment],
      ["primary_muscle", params?.primary_muscle],
      ["force", params?.force],
      ["mechanic", params?.mechanic],
      ["equipment_id", params?.equipment_id],
      ["capability", params?.capability],
      ["available_equipment", params?.available_equipment],
      ["compatibility", params?.compatibility],
    ] as const) {
      for (const value of values ?? []) qs.append(key, value);
    }
    if (params?.is_active) qs.set("is_active", params.is_active);
    if (params?.media) qs.set("media", params.media);
    if (params?.requirement_kind && params.requirement_kind !== "any") {
      qs.set("requirement_kind", params.requirement_kind);
    }
    if (params?.equipment_knowledge && params.equipment_knowledge !== "all") {
      qs.set("equipment_knowledge", params.equipment_knowledge);
    }
    if (params?.assume_unlisted_unavailable) {
      qs.set("assume_unlisted_unavailable", "true");
    }
    if (params?.sort_by) qs.set("sort_by", params.sort_by);
    if (params?.order) qs.set("order", params.order);
    if (params?.with_facets) qs.set("with_facets", "true");
    qs.set("limit", String(params?.limit ?? 50));
    qs.set("offset", String(params?.offset ?? 0));
    return request<ExerciseListResponse>(`/api/v1/exercises?${qs}`);
  },
  exercise: (id: number) => request<ExerciseDetail>(`/api/v1/exercises/${id}`),
  exerciseByExternalId: (externalId: string, source?: string) => {
    const qs = new URLSearchParams();
    if (source) qs.set("source", source);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ExerciseDetail>(
      `/api/v1/exercises/external/${encodeURIComponent(externalId)}${suffix}`
    );
  },
  programs: (params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    qs.set("limit", String(params?.limit ?? 50));
    qs.set("offset", String(params?.offset ?? 0));
    return request<ListResponse<ProgramListItem>>(`/api/v1/programs?${qs}`);
  },
  program: (id: string, version?: number) => {
    const qs = new URLSearchParams();
    if (version) qs.set("version", String(version));
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<ProgramResponse>(`/api/v1/programs/${id}${suffix}`);
  },
  profilePrograms: (profileId: string) =>
    request<ListResponse<ProgramListItem>>(`/api/v1/profiles/${profileId}/programs`),
  generateProgram: (profileId: string, generator: "deterministic" | "ai" = "deterministic") =>
    request<GenerateResponse>(
      `/api/v1/profiles/${profileId}/programs/generate`,
      { method: "POST", body: JSON.stringify({ generator }) },
      LONG_TIMEOUT_MS
    ),
  programHtml: (programId: string, version?: number) => {
    const qs = new URLSearchParams();
    if (version) qs.set("version", String(version));
    const suffix = qs.toString() ? `?${qs}` : "";
    return requestBlob(`/api/v1/programs/${programId}/html${suffix}`);
  },
  aiProviders: () =>
    request<ListResponse<AIProviderForUI>>("/api/v1/ai/providers"),
};
