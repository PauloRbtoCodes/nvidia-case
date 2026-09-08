"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { AXIS_LABEL, api, type BriefingOut, type CompanyDetail } from "@/lib/api";
import { Axes } from "@/components/Axes";
import { BucketChip } from "@/components/Chip";
import { DeltaSection } from "@/components/Delta";
import { Reading } from "@/components/Reading";

/**
 * Perfil da empresa — os cinco minutos antes da ligação.
 *
 * O radar de quatro eixos com evidência clicável é o que sustenta a tese do
 * projeto: nenhuma afirmação existe sem URL e trecho literal, e quem lê confere
 * a fonte em um clique. Sem isso o briefing seria só texto plausível.
 */
export default function EmpresaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [c, setC] = useState<CompanyDetail | null>(null);
  const [briefing, setBriefing] = useState<BriefingOut | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    api.empresa(id).then(setC).catch((e) => setErro(e instanceof Error ? e.message : String(e)));
    api.briefing(id).then(setBriefing).catch(() => setBriefing(null));
  }, [id]);

  if (erro) {
    return (
      <div className="stack">
        <p className="alert" role="alert">
          <strong>Este perfil não carregou.</strong> {erro}
        </p>
        <p><Link href="/fila" className="link-fwd">Voltar para a fila</Link></p>
      </div>
    );
  }
  if (!c) return <p className="empty">Carregando o perfil…</p>;

  const p = c.profile;
  const d = c.defensibility;
  const setor = p.sector?.value;

  return (
    <div className="stack">
      <p style={{ fontSize: 15 }}>
        <Link href="/fila" className="note" style={{ textDecoration: "underline", textUnderlineOffset: 3 }}>
          Voltar para a fila
        </Link>
      </p>

      <div className="stack-sm">
        <h1 style={{ fontVariationSettings: '"wdth" 118' }}>{p.name}</h1>
        <p className="facts">
          {setor ? <span>{setor}</span> : <span>setor não identificado</span>}
          {c.classification ? <span>{c.classification.maturity}</span> : null}
          {p.stage ? <span>{p.stage}</span> : null}
          {p.website ? (
            <a href={p.website} target="_blank" rel="noopener noreferrer"
               style={{ color: "var(--case)", textDecoration: "underline", textUnderlineOffset: 2 }}>
              {p.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
            </a>
          ) : null}
        </p>
        {p.description ? <p className="lede" style={{ fontSize: 16 }}>{p.description}</p> : null}
      </div>

      {!d ? (
        <p className="empty">
          Esta empresa ainda não tem score de defensibilidade — a pipeline foi interrompida
          antes do diagnóstico. Rode a varredura de novo para completar a leitura.
        </p>
      ) : (
        <>
          <section className="section">
            <h2>Leitura geral</h2>
            <div style={{ display: "flex", gap: 40, flexWrap: "wrap", alignItems: "flex-start" }}>
              <div style={{ flex: "1 1 300px", maxWidth: 440 }}>
                <Reading
                  name="Risco de comoditização"
                  value={d.commoditization_risk}
                  confidence={d.global_confidence}
                  suffix="/100"
                  weak
                  aside={`defensibilidade ${Math.round(d.total)}`}
                />
              </div>
              {c.priority ? (
                <div style={{ paddingTop: 4 }}>
                  <BucketChip bucket={c.priority.bucket} big />
                  <p className="note" style={{ marginTop: 6 }}>
                    eixo mais fraco: {AXIS_LABEL[d.weakest_axis] ?? d.weakest_axis}
                  </p>
                </div>
              ) : null}
            </div>
            <p className="note" style={{ marginTop: 16 }}>
              Calculado com os pesos <code>{d.weights_version}</code>.
            </p>
          </section>

          <section className="section">
            <h2>Os quatro eixos</h2>
            <p className="lede" style={{ fontSize: 15, marginBottom: 22 }}>
              Cada eixo traz duas marcas na mesma escala: a barra grossa é a leitura, a fina
              é quanta evidência a sustenta. Abaixo de 0,35 de confiança o número some — não
              sabemos, e isso não é o mesmo que nota baixa.
            </p>
            <Axes axes={d.axes ?? []} weakest={d.weakest_axis} />
          </section>

          <DeltaSection delta={c.delta} />
        </>
      )}

      {c.priority ? (
        <section className="section">
          <h2>Por que esta posição na fila</h2>
          <dl className="kv" style={{ marginBottom: 14 }}>
            <dt>Urgência</dt><dd>{c.priority.urgency.toFixed(0)} de 100</dd>
            <dt>Capacidade de agir</dt><dd>{(c.priority.capacity_to_act ?? 0).toFixed(2)}</dd>
          </dl>
          {c.priority.capacity_rationale ? (
            <p className="note" style={{ fontSize: 15.5, maxWidth: "66ch" }}>
              {c.priority.capacity_rationale}
            </p>
          ) : null}
          {c.priority.recommended_next_step ? (
            <p style={{ marginTop: 12, maxWidth: "66ch" }}>
              <b style={{ fontWeight: 600 }}>Próximo passo.</b> {c.priority.recommended_next_step}
            </p>
          ) : null}
        </section>
      ) : null}

      {c.recommendations && c.recommendations.length > 0 ? (
        <section className="section">
          <h2>O que oferecer</h2>
          <p className="lede" style={{ fontSize: 15, marginBottom: 8 }}>
            A tecnologia sai do eixo mais fraco, não de uma regra por setor — e nenhuma
            recomendação existe sem citação da base NVIDIA.
          </p>
          {c.recommendations.map((r, i) => (
            <article key={i} style={{
              borderTop: "1px solid var(--rule)", paddingTop: 20, marginTop: 20,
            }}>
              <h3 style={{ fontSize: 19, fontVariationSettings: '"wdth" 108' }}>{r.technology}</h3>
              <p className="note" style={{ marginTop: 4 }}>
                endereça {AXIS_LABEL[r.addresses_axis] ?? r.addresses_axis}, prioridade{" "}
                {r.priority}, complexidade {r.complexity}
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 14 }}>
                <p style={{ maxWidth: "66ch" }}>
                  <b style={{ fontWeight: 600 }}>Técnico.</b> {r.technical_rationale}
                </p>
                <p style={{ maxWidth: "66ch" }}>
                  <b style={{ fontWeight: 600 }}>Negócio.</b> {r.business_rationale}
                </p>
                <p style={{ maxWidth: "66ch" }}>
                  <b style={{ fontWeight: 600 }}>Próxima ação.</b> {r.next_action}
                </p>
              </div>
              {r.kb_citations && r.kb_citations.length > 0 ? (
                <div className="evidence" style={{ marginTop: 14 }}>
                  {r.kb_citations.map((k, j) => (
                    <div key={j} style={{ marginTop: j ? 10 : 0 }}>
                      <a href={k.source_url} target="_blank" rel="noopener noreferrer">
                        {k.source_title ?? hostname(k.source_url)}
                      </a>
                      <blockquote>
                        “{k.text.slice(0, 240)}{k.text.length > 240 ? "…" : ""}”
                      </blockquote>
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </section>
      ) : null}

      {briefing ? (
        <p style={{ display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
          <a className="link-fwd" href={`/api${briefing.markdown_url}`}
             target="_blank" rel="noopener noreferrer">
            Abrir o briefing completo
          </a>
          <button type="button" className="btn-ghost no-print" onClick={() => window.print()}>
            Exportar PDF
          </button>
        </p>
      ) : null}
    </div>
  );
}

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
