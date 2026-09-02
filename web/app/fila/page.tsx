"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AXIS_LABEL, api, BUCKET_LABEL, BUCKET_VARS, type Bucket, type QueueItem } from "@/lib/api";
import { BucketChip } from "@/components/Chip";
import { DeltaTag } from "@/components/Delta";
import { IconSeta } from "@/components/Icons";

const ORDEM: Bucket[] = ["abordar_agora", "case_potencial", "nutrir", "monitorar"];

/**
 * Tela 2 — a fila da semana.
 *
 * O produto para o gerente não é uma lista, é uma **ordem**: a posição responde
 * "a quem ligo hoje". Os filtros por bucket existem porque, na prática, quem
 * abre esta tela já sabe que tipo de conversa tem tempo para hoje — "abordar
 * agora" é uma pergunta diferente de "quem eu deveria nutrir esta semana".
 */
export default function FilaPage() {
  const [itens, setItens] = useState<QueueItem[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<Bucket | "todos">("todos");

  useEffect(() => {
    api.fila().then(setItens).catch((e) => setErro(e instanceof Error ? e.message : String(e)));
  }, []);

  const contagem = useMemo(() => {
    const c: Record<string, number> = {};
    for (const it of itens ?? []) c[it.bucket] = (c[it.bucket] ?? 0) + 1;
    return c;
  }, [itens]);

  const visiveis = useMemo(
    () => (itens ?? []).filter((it) => filtro === "todos" || it.bucket === filtro),
    [itens, filtro],
  );

  return (
    <div className="stack">
      <div className="stack-sm">
        <span className="label">Fila de prioridade</span>
        <h1>A quem falar primeiro</h1>
        <p style={{ color: "var(--ink-muted)", maxWidth: "64ch" }}>
          Ordenada por <strong style={{ color: "var(--ink)" }}>urgência = risco × capacidade de agir</strong>,
          ponderada pela confiança do diagnóstico. Vulnerável e capitalizada é conversa
          urgente; vulnerável sem capital é nutrição via comunidade.
        </p>
      </div>

      {erro ? (
        <p className="alert" role="alert"><strong>Não foi possível carregar a fila.</strong> {erro}</p>
      ) : null}

      {itens === null && !erro ? (
        <p className="empty">Carregando…</p>
      ) : itens && itens.length === 0 ? (
        <p className="empty">
          Nenhuma empresa diagnosticada ainda.{" "}
          <Link href="/busca" style={{ color: "var(--accent-ink)", fontWeight: 600 }}>Rodar uma varredura</Link>.
        </p>
      ) : (
        <>
          <div className="filters" role="group" aria-label="Filtrar por prioridade">
            <button
              type="button" className="filter" aria-pressed={filtro === "todos"}
              onClick={() => setFiltro("todos")}
            >
              Todos · {itens?.length ?? 0}
            </button>
            {ORDEM.map((b) => (
              <button
                key={b} type="button" className="filter" aria-pressed={filtro === b}
                onClick={() => setFiltro(b)}
              >
                {BUCKET_LABEL[b]} · {contagem[b] ?? 0}
              </button>
            ))}
          </div>

          {visiveis.length === 0 ? (
            <p className="empty">Nenhuma empresa neste bucket.</p>
          ) : (
            <div className="queue">
              {visiveis.map((it, i) => {
                const v = BUCKET_VARS[it.bucket] ?? BUCKET_VARS.monitorar;
                return (
                  <Link
                    key={it.company_id}
                    href={`/empresa/${it.company_id}`}
                    className="queue-row"
                    style={{ ["--chip-fg" as string]: v.fg, ["--chip-wash" as string]: v.wash }}
                  >
                    <span className="queue-rank tabular">{i + 1}</span>
                    <span>
                      <span className="queue-name">{it.company_name || "sem nome"}</span>
                      <span className="queue-meta">
                        <BucketChip bucket={it.bucket} />
                        {it.sector ? <span>{it.sector}</span> : null}
                        {it.weakest_axis ? (
                          <span>eixo fraco: {AXIS_LABEL[it.weakest_axis] ?? it.weakest_axis}</span>
                        ) : null}
                        <DeltaTag delta={it.delta} />
                      </span>
                      {it.recommended_next_step ? (
                        <span className="queue-next">{it.recommended_next_step}</span>
                      ) : null}
                    </span>
                    <span className="queue-metrics">
                      <span className="metric"><b>{it.urgency.toFixed(1)}</b><span>urgência</span></span>
                      <span className="metric"><b>{Math.round(it.commoditization_risk)}</b><span>risco</span></span>
                      <span className="metric"><b>{it.global_confidence.toFixed(2)}</b><span>confiança</span></span>
                      <span className="queue-go" aria-hidden><IconSeta /></span>
                    </span>
                  </Link>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
