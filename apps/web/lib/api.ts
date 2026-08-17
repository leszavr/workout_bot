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
};
