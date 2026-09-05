import { AXIS_LABEL, type AxisDelta, type ChangeKind, type ScoreDelta } from "@/lib/api";

/**
 * O gatilho temporal na UI: "por que a conversa é agora".
 *
 * A cor nunca carrega o sentido sozinha — cada tipo tem símbolo e rótulo textual,
 * a mesma regra dos buckets. E o invariante do ADR 0002 estendido ao tempo já
 * vem resolvido do backend: sumiço de evidência chega como `confianca_caiu`,
 * nunca como `piorou`, então aqui é só desenhar.
 */
const ESTILO: Record<ChangeKind, { marca: string; rotulo: string; cor: string; wash: string }> = {
  nova_evidencia: { marca: "◆", rotulo: "nova evidência", cor: "var(--accent)", wash: "var(--accent-wash)" },
  melhorou: { marca: "▲", rotulo: "melhorou", cor: "var(--nutrir)", wash: "var(--nutrir-wash)" },
  piorou: { marca: "▼", rotulo: "piorou", cor: "var(--abordar)", wash: "var(--abordar-wash)" },
  confianca_caiu: { marca: "◑", rotulo: "confiança caiu", cor: "var(--monitorar)", wash: "var(--monitorar-wash)" },
  estavel: { marca: "•", rotulo: "estável", cor: "var(--monitorar)", wash: "var(--monitorar-wash)" },
};

function texto(a: AxisDelta): string {
  const eixo = AXIS_LABEL[a.axis] ?? a.axis;
  if (a.kind === "nova_evidencia") return `${eixo}: sinal novo`;
  if (a.kind === "confianca_caiu") return `${eixo}: rastro do sinal enfraqueceu`;
  const sinal = a.score_change > 0 ? `+${a.score_change}` : `${a.score_change}`;
  return `${eixo} ${sinal}`;
}

/** Compacto, para a linha da fila. Mostra só a manchete. */
export function DeltaTag({ delta }: { delta?: ScoreDelta | null }) {
  const a = delta?.headline_axis;
  if (!a || !delta?.has_changes) return null;
  const e = ESTILO[a.kind] ?? ESTILO.estavel;
  return (
    <span
      className="delta-tag"
      style={{ ["--delta-fg" as string]: e.cor, ["--delta-wash" as string]: e.wash }}
      title={`Desde a execução anterior — ${e.rotulo}`}
    >
      <span aria-hidden>{e.marca}</span>
      {texto(a)}
    </span>
  );
}

/** Seção "o que mudou" no perfil: todos os eixos que se moveram. */
export function DeltaSection({ delta }: { delta?: ScoreDelta | null }) {
  const moveram = (delta?.axes ?? []).filter((a) => a.kind !== "estavel");
  if (!delta || moveram.length === 0) return null;

  const ordem: ChangeKind[] = ["nova_evidencia", "piorou", "melhorou", "confianca_caiu"];
  moveram.sort((x, y) => ordem.indexOf(x.kind) - ordem.indexOf(y.kind));

  return (
    <section className="card stack-sm">
      <div className="section-head"><h2>O que mudou</h2></div>
      <p style={{ fontSize: 13.5, color: "var(--ink-muted)", maxWidth: "64ch" }}>
        Diff contra a execução anterior. Sinal que sumiu de uma coleta para a
        outra aparece como <em>confiança caiu</em> — nunca como piora do eixo:
        página fora do ar não é regressão da empresa.
      </p>
      <ul className="delta-list">
        {moveram.map((a) => {
          const e = ESTILO[a.kind] ?? ESTILO.estavel;
          return (
            <li key={a.axis}>
              <span
                className="delta-tag"
                style={{ ["--delta-fg" as string]: e.cor, ["--delta-wash" as string]: e.wash }}
              >
                <span aria-hidden>{e.marca}</span>
                {e.rotulo}
              </span>
              <span className="delta-line">
                {texto(a)}
                <span className="delta-nums tabular">
                  {" "}
                  {Math.round(a.score_before)}→{Math.round(a.score_after)} · conf{" "}
                  {a.confidence_before.toFixed(2)}→{a.confidence_after.toFixed(2)}
                </span>
              </span>
              {a.kind === "nova_evidencia" && a.new_evidences && a.new_evidences.length > 0 ? (
                <div className="evidence" style={{ marginTop: 6 }}>
                  {a.new_evidences.slice(0, 3).map((ev, i) => (
                    <div key={i} style={{ marginTop: i ? 8 : 0 }}>
                      <a href={ev.url} target="_blank" rel="noopener noreferrer">
                        {(() => {
                          try {
                            return new URL(ev.url).hostname.replace(/^www\./, "");
                          } catch {
                            return ev.url;
                          }
                        })()}
                      </a>
                      <blockquote>“{ev.excerpt.slice(0, 200)}{ev.excerpt.length > 200 ? "…" : ""}”</blockquote>
                    </div>
                  ))}
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
