"use client";

// /ai/tasks — где именно используется ИИ.
//
// Включение задачи, параметры обращения, порядок моделей (основная →
// резервные) и версия инструкции. Показываются только задачи, которые
// система действительно умеет выполнять.

import TasksSection from "@/components/ai/TasksSection";
import { Card, Notice, Skeleton } from "@/components/ui/Primitives";
import { useAIConfiguration } from "@/lib/aiData";
import { useCurrentUser } from "@/lib/session";

export default function AITasksPage() {
  const { canWrite } = useCurrentUser();
  const state = useAIConfiguration();

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

      <TasksSection
        tasks={state.tasks}
        allModels={state.allModels}
        endpoints={state.endpoints}
        providers={state.providers}
        prompts={state.prompts}        canWrite={canWrite}
        onChanged={state.onChanged}
        onError={state.onError}
      />
    </>
  );
}
