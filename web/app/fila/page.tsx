"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AXIS_LABEL, api, BUCKET_LABEL, type Bucket, type QueueItem } from "@/lib/api";
import { BucketChip } from "@/components/Chip";
import { DeltaTag } from "@/components/Delta";
import { MiniReading } from "@/components/Reading";

const ORDEM: Bucket[] = ["abordar_agora", "case_potencial", "nutrir", "monitorar"];

/**
 * A fila da semana — a tela que responde "a quem ligo hoje".
 *
 * É um registro, não uma lista de cartões: a posição é o produto, então a ordem
 * precisa ser lida de cima para baixo sem que cada linha peça atenção igual. A
 * numeração é legítima aqui e só aqui, porque o conteúdo é mesmo uma sequência.
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
    for (const it of itens ?? []) if (it.bucket) c[it.bucket] = (c[it.bucket] ?? 0) + 1;
    return c;
  }, [itens]);

  const mudaram = useMemo(
    () => (itens ?? []).filter((it) => it.delta?.has_changes).length,
    [itens],
  );

  const visiveis = useMemo(
    () => (itens ?? []).filter((it) => filtro === "todos" || it.bucket === filtro),
    [itens, filtro],
  );

  return (
    <div className="stack">
      <div className="stack-sm">
        <h1>A quem falar primeiro</h1>
        <p className="lede">
          Ordenada por urgência: o risco de comoditização multiplicado pela capacidade de
          reagir, ponderada pela confiança do diagnóstico. Vulnerável e capitalizada é
          conversa urgente; vulnerável sem capital é nutrição pela comunidade.
        </p>
        {itens && itens.length > 0 ? (
          <p className="note">
            {itens.length} {itens.length === 1 ? "empresa diagnosticada" : "empresas diagnosticadas"}
            {mudaram > 0
              ? `, ${mudaram} com mudança desde a varredura anterior`
              : ", nenhuma mudou desde a varredura anterior"}.
          </p>
        ) : null}
      </div>

      {erro ? (
        <p className="alert" role="alert">
          <strong>A fila não carregou.</strong> {erro} Confira se a API está no ar
          (<code>make api</code>).
        </p>
      ) : null}

      {itens === null && !erro ? (
        <p className="empty">Carregando a fila…</p>
      ) : itens && itens.length === 0 ? (
        <p className="empty">
          Nenhuma empresa diagnosticada ainda.{" "}
          <Link href="/busca" className="link-fwd">Rodar a primeira varredura</Link>.
        </p>
      ) : (
        <div>
          <div className="chips" role="group" aria-label="Filtrar por decisão de agenda"
               style={{ marginBottom: 4 }}>
            <button type="button" className="chip-btn" aria-pressed={filtro === "todos"}
                    onClick={() => setFiltro("todos")}>
              Todas as {itens?.length ?? 0}
            </button>
            {ORDEM.filter((b) => contagem[b]).map((b) => (
              <button key={b} type="button" className="chip-btn" aria-pressed={filtro === b}
                      onClick={() => setFiltro(b)}>
                {BUCKET_LABEL[b]} ({contagem[b]})
              </button>
            ))}
          </div>

          {visiveis.length === 0 ? (
            <p className="empty">Nenhuma empresa com essa decisão de agenda no momento.</p>
          ) : (
            <div className="register">
              {visiveis.map((it, i) => (
                <Link key={it.company_id} href={`/empresa/${it.company_id}`} className="entry">
                  <span className="entry-rank num" aria-hidden>{i + 1}</span>

                  <span>
                    <span className="entry-name">{it.company_name || "empresa sem nome"}</span>
                    <span className="entry-meta">
                      {it.bucket ? <BucketChip bucket={it.bucket} /> : null}
                      <span>eixo mais fraco: {AXIS_LABEL[it.weakest_axis] ?? it.weakest_axis}</span>
                      {typeof it.urgency === "number" ? (
                        <span className="num">urgência {it.urgency.toFixed(0)}</span>
                      ) : (
                        <span>ainda sem priorização</span>
                      )}
                    </span>
                    {it.delta?.has_changes ? (
                      <span className="entry-trigger"><DeltaTag delta={it.delta} /></span>
                    ) : null}
                  </span>

                  <span className="entry-readings">
                    <MiniReading
                      label="risco de comoditização"
                      value={it.commoditization_risk}
                      confidence={it.global_confidence}
                      weak
                    />
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
