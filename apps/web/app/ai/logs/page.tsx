"use client";

// /ai/logs — почему что-то не работает.
//
// Три журнала в одном месте: вызовы AI (токены, задержки, ошибки), причины
// fallback и изменения конфигурации. Разбор инцидента начинается здесь.

import AIFallbackEvents from "@/components/AIFallbackEvents";
import AIObservability from "@/components/AIObservability";
import { useAIConfiguration } from "@/lib/aiData";

export default function AILogsPage() {
  const state = useAIConfiguration();

  if (state.loading) {
    return (
      <div className="card">
        <p className="muted" style={{ margin: 0 }}>
          Загрузка журналов...
        </p>
      </div>
    );
  }

  return (
    <>
      {state.error && <div className="error">{state.error}</div>}

      <AIFallbackEvents reloadKey={state.reloadKey} onError={state.onError} />

      <AIObservability
        usage={state.usage}
        audit={state.audit}
        models={state.allModels}
        providers={state.providers}
        refreshing={state.refreshing}
        onRefresh={() => state.reload().catch(() => undefined)}
      />
    </>
  );
}
