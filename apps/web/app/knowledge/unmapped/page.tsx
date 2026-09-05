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

import AppNav from "@/components/AppNav";
import KnowledgeTabs from "@/components/knowledge/KnowledgeTabs";
import { Pagination } from "@/components/ui/Pagination";
import { Card, Empty, Skeleton, Status } from "@/components/ui/Primitives";
import { UnmappedValue, getToken, knowledgeApi } from "@/lib/api";
import { UNMAPPED_REASON_LABELS, count } from "@/lib/labels";

const PAGE_SIZE = 100;

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
    if (!getToken()) {
      window.location.href = "/login";
      return;
    }
    load(offset).catch(() => undefined);
  }, [offset, load]);

  return (
    <div className="app-shell">
      <AppNav />
      <main className="main">
        <div className="page-head">
          <h1 className="page-title">Незакрытые значения</h1>
          <p className="page-subtitle">
            Значения оборудования из справочника упражнений, которым не нашлось
            записи в словаре. Они сохранены, а не отброшены: по таким
            упражнениям система отвечает «неизвестно».
          </p>
        </div>

        <KnowledgeTabs />

        {error && <div className="error">{error}</div>}

        <Card
          title="Список"
          description={`Записей: ${count(total)}`}
        >
          {loading && <Skeleton rows={6} />}

          {!loading && items.length === 0 && (
            <Empty
              title="Незакрытых значений нет"
              hint="Каждое значение оборудования справочника сопоставлено со словарём."
            />
          )}

          {!loading && items.length > 0 && (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Упражнение</th>
                      <th>Значение источника</th>
                      <th>Почему</th>
                      <th>Пояснение</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr
                        key={`${item.exercise_external_id}-${item.raw_value}`}
                      >
                        <td>
                          <Link
                            href={`/exercises?search=${encodeURIComponent(item.exercise_external_id)}`}
                          >
                            {item.exercise_external_id}
                          </Link>
                        </td>
                        <td>
                          <code>{item.raw_value}</code>
                        </td>
                        <td>
                          <Status
                            tone={
                              item.reason === "ambiguous" ? "warn" : "neutral"
                            }
                          >
                            {UNMAPPED_REASON_LABELS[item.reason] ?? item.reason}
                          </Status>
                        </td>
                        <td>
                          {item.notes ?? <span className="muted">—</span>}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pagination
                total={total}
                limit={PAGE_SIZE}
                offset={offset}
                onChange={setOffset}
                disabled={loading}
              />
            </>
          )}
        </Card>
      </main>
    </div>
  );
}
