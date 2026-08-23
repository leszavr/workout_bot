"use client";

// /ai — обзор: работает ли AI прямо сейчас и что делать, если нет.
//
// Первое, что видит администратор: готовность задачи, состояние
// инфраструктуры и мастер быстрого подключения. Экспертное управление
// провайдерами и задачами живёт на отдельных подстраницах, чтобы обзор
// не превращался в простыню.

import AIFallbackEvents from "@/components/AIFallbackEvents";
import AIInfrastructureHealthPanel from "@/components/AIInfrastructureHealthPanel";
import AIQuickSetup from "@/components/AIQuickSetup";
import AIReadinessPanel from "@/components/AIReadinessPanel";
import { Notice } from "@/components/ui/Primitives";
import { MAIN_TASK_TYPE, useAIConfiguration } from "@/lib/aiData";
import { useCurrentUser } from "@/lib/session";

export default function AIOverviewPage() {
  const { canWrite } = useCurrentUser();
  const state = useAIConfiguration();

  return (
    <>
      {state.error && <div className="error">{state.error}</div>}
      {state.notice && <Notice tone="ok">{state.notice}</Notice>}

      <AIReadinessPanel
        report={state.readiness}
        refreshing={state.refreshing}
        onRefresh={() => state.reload().catch(() => undefined)}
      />

      <AIInfrastructureHealthPanel
        reloadKey={state.reloadKey}
        onError={state.onError}
      />

      {/* Мастер — основной путь настройки, поэтому он на обзоре, а не спрятан
          в экспертном разделе. Для viewer он бесполезен: сервер откажет. */}
      {canWrite && (
        <AIQuickSetup
          taskType={MAIN_TASK_TYPE}
          onFinished={state.onChanged}
          onError={state.onError}
        />
      )}

      <AIFallbackEvents reloadKey={state.reloadKey} onError={state.onError} />
    </>
  );
}
