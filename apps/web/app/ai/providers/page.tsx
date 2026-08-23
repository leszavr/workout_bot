"use client";

// /ai/providers — экспертное управление подключениями.
//
// Здесь живёт дерево провайдер → эндпоинт → модель: создание, правка,
// включение/отключение, проверка подключения и безопасное удаление.

import { useState } from "react";

import ProvidersSection from "@/components/ai/ProvidersSection";
import { Card, Notice, Skeleton } from "@/components/ui/Primitives";
import { AIEndpointTestResult } from "@/lib/api";
import { useAIConfiguration } from "@/lib/aiData";
import { useCurrentUser } from "@/lib/session";

export default function AIProvidersPage() {
  const { canWrite } = useCurrentUser();
  const state = useAIConfiguration();
  const [testResults, setTestResults] = useState<
    Record<number, AIEndpointTestResult>
  >({});

  if (state.loading) {
    return (
      <Card>
        <Skeleton rows={4} />
      </Card>
    );
  }

  return (
    <>
      {state.error && <div className="error">{state.error}</div>}
      {state.notice && <Notice tone="ok">{state.notice}</Notice>}

      <ProvidersSection
        providers={state.providers}
        endpoints={state.endpoints}
        models={state.models}
        testResults={testResults}
        canWrite={canWrite}
        onChanged={state.onChanged}
        onError={state.onError}
        onTestResult={(endpointId, result) =>
          setTestResults((prev) => ({ ...prev, [endpointId]: result }))
        }
      />
    </>
  );
}
