"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AXIS_LABEL, api, BUCKET_VARS, type QueueItem } from "@/lib/api";
import { BucketChip } from "@/components/Chip";

/**
 * Tela 2 — a fila da semana.
 *
 * O produto para o gerente não é uma lista de empresas, é uma **fila**: a
 * ordem responde "a quem ligo hoje". Por isso a urgência aparece como número
 * grande e o bucket como pastilha — os dois juntos dizem se a conversa é desta
 * semana ou trabalho de comunidade.
 */
export default function FilaPage() {
  const [itens, setItens] = useState<QueueItem[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    api.fila().then(setItens).catch((e) => setErro(e instanceof Error ? e.message : String(e)));
  }, []);

  return (
    <div className="stack">
      <div className="stack-sm">
        <span className="label">Fila de prioridade</span>
        <h1>A quem falar primeiro</h1>
        <p style={{ color: "var(--ink-muted)", maxWidth: "62ch" }}>
          Ordenada por <strong style={{ color: "var(--ink)" }}>urgência = risco × capacidade de agir</strong>,
          ponderada pela confiança do diagnóstico. Vulnerável e capitalizada é conversa
          urgente; vulnerável sem capital é nutrição via comunidade.
        </p>
      </div>

      {erro ? (
        <p className="alert" role="alert">
          <strong>Não foi possível carregar a fila.</strong> {erro}
        </p>
      ) : null}

      {itens === null && !erro ? (
        <p className="empty">Carregando…</p>
      ) : itens && itens.length === 0 ? (
        <p className="empty">
          Nenhuma empresa diagnosticada ainda.{" "}
          <Link href="/" style={{ color: "var(--accent)", fontWeight: 600 }}>
            Rodar uma varredura
          </Link>
          .
        </p>
      ) : (
        <div className="queue">
          {(itens ?? []).map((it, i) => {
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
                  </span>
                </span>
                <span className="queue-metrics">
                  <span className="metric">
                    <b>{it.urgency.toFixed(1)}</b>
                    <span>urgência</span>
                  </span>
                  <span className="metric">
                    <b>{Math.round(it.commoditization_risk)}</b>
                    <span>risco</span>
                  </span>
                  <span className="metric">
                    <b>{it.global_confidence.toFixed(2)}</b>
                    <span>confiança</span>
                  </span>
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
