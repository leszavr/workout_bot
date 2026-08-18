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
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, cache: "no-store" });

  if (response.status === 401) {
    clearToken();
    if (typeof window !== "undefined") window.location.href = "/login";
    throw new ApiError(401, "Требуется авторизация");
  }
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new ApiError(response.status, detail || `Ошибка ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function login(loginName: string, password: string): Promise<void> {
  const body = await request<{ access_token: string }>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ login: loginName, password }),
  });
  setToken(body.access_token);
}

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
  model: string;
  message?: string;
  error_type?: string;
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
    request<AIEndpointTestResult>(`/api/v1/admin/ai/endpoints/${id}/test`, {
      method: "POST",
    }),
  models: (endpointId: number) =>
    request<ListResponse<AIModelItem>>(
      `/api/v1/admin/ai/endpoints/${endpointId}/models`
    ),
  createModel: (endpointId: number, body: Record<string, unknown>) =>
    request<AIModelItem>(`/api/v1/admin/ai/endpoints/${endpointId}/models`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
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
    request<GenerateResponse>(`/api/v1/profiles/${profileId}/programs/generate`, {
      method: "POST",
      body: JSON.stringify({ generator }),
    }),
  aiProviders: () =>
    request<ListResponse<AIProviderForUI>>("/api/v1/ai/providers"),
};
