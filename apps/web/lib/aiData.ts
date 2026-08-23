"use client";

// Общая загрузка AI-конфигурации для подстраниц раздела /ai.
//
// Раньше вся страница была одним файлом и грузила всё сразу. После разделения
// на подстраницы каждая берёт из этого хука только нужное — но логика загрузки
// и порядок обхода provider → endpoint → model остаются в одном месте.
//
// `reloadKey` растёт после каждой изменяющей операции: панели состояния и
// журналы обновляются с backend, а не показывают устаревшие данные.

import { useCallback, useEffect, useState } from "react";

import {
  AIAuditItem,
  AIEndpointItem,
  AIModelItem,
  AIProviderItem,
  AIReadinessReport,
  AITaskItem,
  AIUsageItem,
  aiApi,
} from "@/lib/api";

export const MAIN_TASK_TYPE = "workout_generation";

export interface AIConfigurationState {
  providers: AIProviderItem[];
  // Ключ — id провайдера / эндпоинта: дерево строится из реальной конфигурации.
  endpoints: Record<number, AIEndpointItem[]>;
  models: Record<number, AIModelItem[]>;
  tasks: AITaskItem[];
  promptVersions: number[];
  readiness: AIReadinessReport | null;
  usage: AIUsageItem[];
  audit: AIAuditItem[];
  allModels: AIModelItem[];
  loading: boolean;
  refreshing: boolean;
  error: string;
  notice: string;
  reloadKey: number;
  reload: () => Promise<void>;
  // Вызывается после успешной операции: показывает сообщение и перезагружает.
  onChanged: (message: string) => void;
  onError: (message: string) => void;
}

export function useAIConfiguration(): AIConfigurationState {
  const [providers, setProviders] = useState<AIProviderItem[]>([]);
  const [endpoints, setEndpoints] = useState<Record<number, AIEndpointItem[]>>({});
  const [models, setModels] = useState<Record<number, AIModelItem[]>>({});
  const [tasks, setTasks] = useState<AITaskItem[]>([]);
  const [promptVersions, setPromptVersions] = useState<number[]>([]);
  const [readiness, setReadiness] = useState<AIReadinessReport | null>(null);
  const [usage, setUsage] = useState<AIUsageItem[]>([]);
  const [audit, setAudit] = useState<AIAuditItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const reload = useCallback(async () => {
    setRefreshing(true);
    try {
      const [providersData, tasksData, prompts, report, usageData, auditData] =
        await Promise.all([
          aiApi.providers(),
          aiApi.tasks(),
          aiApi.prompts(MAIN_TASK_TYPE),
          aiApi.readiness(MAIN_TASK_TYPE),
          aiApi.usage(),
          aiApi.audit(),
        ]);

      const endpointMap: Record<number, AIEndpointItem[]> = {};
      const modelMap: Record<number, AIModelItem[]> = {};
      for (const provider of providersData.items) {
        const eps = await aiApi.endpoints(provider.id);
        endpointMap[provider.id] = eps.items;
        for (const endpoint of eps.items) {
          modelMap[endpoint.id] = (await aiApi.models(endpoint.id)).items;
        }
      }

      setProviders(providersData.items);
      setEndpoints(endpointMap);
      setModels(modelMap);
      setTasks(tasksData.items);
      setPromptVersions(
        prompts.items.filter((p) => p.enabled).map((p) => p.version)
      );
      setReadiness(report);
      setUsage(usageData.items);
      setAudit(auditData.items);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    reload().catch(() => undefined);
  }, [reload]);

  const onError = useCallback((message: string) => setError(message), []);

  const onChanged = useCallback(
    (message: string) => {
      setNotice(message);
      window.setTimeout(() => setNotice(""), 6000);
      setReloadKey((value) => value + 1);
      reload().catch(() => undefined);
    },
    [reload]
  );

  return {
    providers,
    endpoints,
    models,
    tasks,
    promptVersions,
    readiness,
    usage,
    audit,
    allModels: Object.values(models).flat(),
    loading,
    refreshing,
    error,
    notice,
    reloadKey,
    reload,
    onChanged,
    onError,
  };
}
