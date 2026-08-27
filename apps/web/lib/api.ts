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
  difficulty: string | null;
  exercise_type: string | null;
  source: string;
  is_active: boolean;
}

export interface ExerciseMediaItem {
  sequence: number;
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
  secondary_muscles: string[];
  force: string | null;
  mechanic: string | null;
  contraindications: string[];
  limitations: string[];
  images: string[];
  media?: ExerciseMediaItem[];
}

export interface ListResponse<T> {
  total: number;
  items: T[];
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

export interface AIPromptItem {
  id: number;
  task_type: string;
  version: number;
  name: string;
  enabled: boolean;
  created_at: string | null;
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
  detail: string;
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
  infrastructureHealth: () =>
    request<AIInfrastructureHealth>("/api/v1/admin/ai/infrastructure-health"),
  refreshInfrastructureHealth: () =>
    request<AIInfrastructureHealth>(
      "/api/v1/admin/ai/infrastructure-health/refresh",
      { method: "POST" }
    ),
  prompts: (taskType = "workout_generation") =>
    request<ListResponse<AIPromptItem> & { next_version: number }>(
      `/api/v1/admin/ai/prompts/${taskType}`
    ),
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

export const api = {
  dashboard: () => request<Dashboard>("/api/v1/dashboard"),
  profiles: (params?: { search?: string; status?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    if (params?.status) qs.set("status", params.status);
    qs.set("limit", String(params?.limit ?? 50));
    qs.set("offset", String(params?.offset ?? 0));
    return request<ListResponse<ProfileListItem>>(`/api/v1/profiles?${qs}`);
  },
  profile: (id: string) => request<ProfileDetail>(`/api/v1/profiles/${id}`),
  exercises: (params?: { search?: string; exercise_type?: string; difficulty?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("search", params.search);
    if (params?.exercise_type) qs.set("exercise_type", params.exercise_type);
    if (params?.difficulty) qs.set("difficulty", params.difficulty);
    qs.set("limit", String(params?.limit ?? 50));
    qs.set("offset", String(params?.offset ?? 0));
    return request<ListResponse<ExerciseListItem>>(`/api/v1/exercises?${qs}`);
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
  aiProviders: () =>
    request<ListResponse<AIProviderForUI>>("/api/v1/ai/providers"),
};
