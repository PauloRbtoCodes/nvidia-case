"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { AXIS_LABEL, api, type BriefingOut, type CompanyDetail } from "@/lib/api";
import { Axes } from "@/components/Axes";
import { BucketChip } from "@/components/Chip";

/**
 * Tela 3 — perfil da empresa.
 *
 * O radar de quatro eixos com evidências clicáveis é o que sustenta a tese do
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
          <strong>Não foi possível carregar a empresa.</strong> {erro}
        </p>
        <p><Link href="/fila" style={{ color: "var(--accent)" }}>← Voltar para a fila</Link></p>
      </div>
    );
  }
  if (!c) return <p className="empty">Carregando…</p>;

  const p = c.profile;
  const d = c.defensibility;
  const setor = p.sector?.value;

  return (
    <div className="stack">
      <p style={{ fontSize: 14 }}>
        <Link href="/fila" style={{ color: "var(--ink-muted)" }}>← Fila</Link>
      </p>

      <div className="stack-sm">
        <span className="label">
          {setor ?? "setor não identificado"}
          {c.classification ? ` · ${c.classification.maturity}` : ""}
          {c.classification ? ` · confiança ${c.classification.confidence.toFixed(2)}` : ""}
        </span>
        <h1>{p.name}</h1>
        {p.website ? (
          <p style={{ fontSize: 14 }}>
            <a href={p.website} target="_blank" rel="noopener noreferrer"
               style={{ color: "var(--case)", textDecoration: "underline", textUnderlineOffset: 2 }}>
              {p.website.replace(/^https?:\/\//, "").replace(/\/$/, "")}
            </a>
          </p>
        ) : null}
        {p.description ? (
          <p style={{ color: "var(--ink-muted)", maxWidth: "68ch" }}>{p.description}</p>
        ) : null}
      </div>

      {!d ? (
        <p className="empty">
          Esta empresa ainda não tem score de defensibilidade — a pipeline foi
          interrompida antes do diagnóstico.
        </p>
      ) : (
        <>
          <section className="card stack-sm">
            <div style={{ display: "flex", gap: 30, flexWrap: "wrap", alignItems: "flex-start" }}>
              <span className="metric" style={{ textAlign: "left" }}>
                <b style={{ fontSize: 30 }}>{Math.round(d.total)}</b>
                <span>defensibilidade</span>
              </span>
              <span className="metric" style={{ textAlign: "left" }}>
                <b style={{ fontSize: 30 }}>{Math.round(d.commoditization_risk)}</b>
                <span>risco de comoditização</span>
              </span>
              <span className="metric" style={{ textAlign: "left" }}>
                <b style={{ fontSize: 30 }}>{d.global_confidence.toFixed(2)}</b>
                <span>confiança global</span>
              </span>
              {c.priority ? (
                <span style={{ marginLeft: "auto", alignSelf: "center" }}>
                  <BucketChip bucket={c.priority.bucket} />
                </span>
              ) : null}
            </div>
            <p style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>
              Pesos <code className="mono">{d.weights_version}</code> · eixo mais fraco:{" "}
              {AXIS_LABEL[d.weakest_axis] ?? d.weakest_axis}
            </p>
          </section>

          <section className="card stack-sm">
            <h2>Defensibility Radar</h2>
            <p style={{ fontSize: 13.5, color: "var(--ink-muted)", maxWidth: "64ch" }}>
              Score e confiança são números independentes. Eixo com confiança abaixo de
              0,35 aparece como <em>evidência insuficiente</em> — nunca como nota baixa.
            </p>
            <div style={{ marginTop: 8 }}>
              <Axes axes={d.axes ?? []} weakest={d.weakest_axis} />
            </div>
          </section>
        </>
      )}

      {c.priority ? (
        <section className="card stack-sm">
          <h2>Prioridade</h2>
          <div className="table-wrap">
            <table>
              <tbody>
                <tr><th scope="row">Urgência</th><td className="num">{c.priority.urgency.toFixed(1)}</td></tr>
                <tr><th scope="row">Capacidade de agir</th><td className="num">{(c.priority.capacity_to_act ?? 0).toFixed(2)}</td></tr>
              </tbody>
            </table>
          </div>
          {c.priority.capacity_rationale ? (
            <p style={{ fontSize: 13.5, color: "var(--ink-muted)" }}>{c.priority.capacity_rationale}</p>
          ) : null}
          {c.priority.recommended_next_step ? (
            <p style={{ fontSize: 14.5 }}>
              <strong>Próximo passo.</strong> {c.priority.recommended_next_step}
            </p>
          ) : null}
        </section>
      ) : null}

      {c.recommendations && c.recommendations.length > 0 ? (
        <section className="card stack-sm">
          <h2>Recomendações NVIDIA</h2>
          <p style={{ fontSize: 13.5, color: "var(--ink-muted)", maxWidth: "64ch" }}>
            A tecnologia sai do eixo mais fraco, não de uma regra por setor — e nenhuma
            recomendação existe sem citação da base de conhecimento.
          </p>
          {c.recommendations.map((r, i) => (
            <article key={i} className="stack-sm" style={{
              borderTop: "1px solid var(--rule)", paddingTop: 16, marginTop: i ? 4 : 8,
            }}>
              <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
                <h3>{r.technology}</h3>
                <span style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>
                  endereça {AXIS_LABEL[r.addresses_axis] ?? r.addresses_axis} ·
                  prioridade {r.priority} · complexidade {r.complexity}
                </span>
              </div>
              <p style={{ fontSize: 14.5 }}><strong>Técnico.</strong> {r.technical_rationale}</p>
              <p style={{ fontSize: 14.5 }}><strong>Negócio.</strong> {r.business_rationale}</p>
              <p style={{ fontSize: 14.5 }}><strong>Próxima ação.</strong> {r.next_action}</p>
              {r.kb_citations && r.kb_citations.length > 0 ? (
                <div className="evidence">
                  <span className="label" style={{ display: "block", marginBottom: 4 }}>
                    Fontes na base NVIDIA
                  </span>
                  {r.kb_citations.map((k, j) => (
                    <div key={j} style={{ marginTop: j ? 8 : 0 }}>
                      <a href={k.source_url} target="_blank" rel="noopener noreferrer">
                        {k.source_title ?? new URL(k.source_url).hostname.replace(/^www\./, "")}
                      </a>
                      <blockquote>“{k.text.slice(0, 240)}{k.text.length > 240 ? "…" : ""}”</blockquote>
                    </div>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </section>
      ) : null}

      {briefing ? (
        <p>
          <a className="btn" href={`/api${briefing.markdown_url}`} target="_blank" rel="noopener noreferrer">
            Abrir briefing completo (markdown)
          </a>
        </p>
      ) : null}
    </div>
  );
}
