"use client";

// /ai/prompts — чем именно система инструктирует модель.
//
// Отдельная вкладка, а не блок внутри «Задач»: задача отвечает на вопрос «какие
// модели и с какими параметрами вызывать», а инструкция — «что и в каком виде
// у них просить». Смешивать их в одной форме значит прятать текст промпта под
// полем «версия инструкции».

import PromptsSection from "@/components/ai/PromptsSection";
import { Notice } from "@/components/ui/Primitives";
import { MAIN_TASK_TYPE, useAIConfiguration } from "@/lib/aiData";
import { useCurrentUser } from "@/lib/session";

export default function AIPromptsPage() {
  const { canWrite } = useCurrentUser();
  const state = useAIConfiguration();

  return (
    <>
      {state.error && <div className="error">{state.error}</div>}
      {state.notice && <Notice tone="ok">{state.notice}</Notice>}

      <PromptsSection
        taskType={MAIN_TASK_TYPE}
        reloadKey={state.reloadKey}
        canWrite={canWrite}
        onChanged={state.onChanged}
        onError={state.onError}
      />
    </>
  );
}
