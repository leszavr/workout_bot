"use client";

// База знаний: незакрытые значения оборудования.
//
// Раздел существует, чтобы потеря информации была невозможна. Значение
// источника, которому не нашлось записи словаря, сохраняется здесь, а не
// отбрасывается: иначе упражнение осталось бы без требований и считалось бы
// выполнимым где угодно.
//
// «Требует уточнения» и «нет в словаре» — разные случаи. Первый означает, что
// источник не сказал, какое именно оборудование нужно (`other`), либо значение
// указывает сразу на несколько записей. Второй — что словарь такого значения не
// знает, и его нужно пополнить.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { KNOWLEDGE_TABS } from "@/components/knowledge/tabs";
import { PageHeader } from "@/components/layout/PageHeader";
import { DataColumn, DataTable } from "@/components/ui/DataTable";
import { PageTabs } from "@/components/ui/PageTabs";
import { Card, Status } from "@/components/ui/Primitives";
import { UnmappedValue, knowledgeApi } from "@/lib/api";
import { UNMAPPED_REASON_LABELS, count } from "@/lib/labels";

const PAGE_SIZE = 100;

const COLUMNS: ReadonlyArray<DataColumn<UnmappedValue>> = [
  {
    key: "exercise",
    header: "Упражнение",
    render: (item) => (
      <Link
        href={`/exercises?search=${encodeURIComponent(item.exercise_external_id)}`}
      >
        {item.exercise_external_id}
      </Link>
    ),
  },
  {
    key: "value",
    header: "Значение источника",
    render: (item) => <code>{item.raw_value}</code>,
  },
  {
    key: "reason",
    header: "Почему",
    hint: "«Требует уточнения» — источник не назвал конкретное оборудование; «нет в словаре» — словарь нужно пополнить.",
    render: (item) => (
      <Status tone={item.reason === "ambiguous" ? "warn" : "neutral"}>
        {UNMAPPED_REASON_LABELS[item.reason] ?? item.reason}
      </Status>
    ),
  },
  {
    key: "notes",
    header: "Пояснение",
    render: (item) => item.notes ?? <span className="muted">—</span>,
  },
];

export default function KnowledgeUnmappedPage() {
  const [items, setItems] = useState<UnmappedValue[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (page: number) => {
    setLoading(true);
    try {
      const response = await knowledgeApi.unmapped({
        limit: PAGE_SIZE,
        offset: page,
      });
      setItems(response.items);
      setTotal(response.total);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load(offset).catch(() => undefined);
  }, [offset, load]);

  return (
    <>
      <PageHeader
        title="Незакрытые значения"
        description="Значения оборудования из справочника упражнений, которым не нашлось записи в словаре. Они сохранены, а не отброшены: по таким упражнениям система отвечает «неизвестно»."
        tabs={
          <PageTabs
            tabs={KNOWLEDGE_TABS}
            root="/knowledge"
            label="Разделы базы знаний"
          />
        }
      />

      <Card title="Список" description={`Записей: ${count(total)}`}>
        <DataTable
          columns={COLUMNS}
          rows={items}
          rowKey={(item) => `${item.exercise_external_id}-${item.raw_value}`}
          loading={loading}
          error={error}
          emptyTitle="Незакрытых значений нет"
          emptyHint="Каждое значение оборудования справочника сопоставлено со словарём."
          pagination={{
            total,
            limit: PAGE_SIZE,
            offset,
            onChange: setOffset,
          }}
        />
      </Card>
    </>
  );
}
