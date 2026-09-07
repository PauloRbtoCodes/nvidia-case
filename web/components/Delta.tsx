import { AXIS_LABEL, type AxisDelta, type ChangeKind, type ScoreDelta } from "@/lib/api";

/**
 * O gatilho temporal: "por que a conversa é agora".
 *
 * Cor nunca sozinha — cada tipo de mudança tem símbolo e palavra, como os
 * buckets. E o invariante do ADR 0002 no eixo do tempo já vem resolvido do
 * backend: sinal que sumiu entre duas coletas chega como `confianca_caiu`,
 * nunca como `piorou`, porque página fora do ar não é regressão da empresa.
 */
const ESTILO: Record<ChangeKind, { marca: string; rotulo: string; cor: string }> = {
  nova_evidencia: { marca: "◆", rotulo: "sinal novo", cor: "var(--green-ink)" },
  melhorou: { marca: "▲", rotulo: "subiu", cor: "var(--nutrir)" },
  piorou: { marca: "▼", rotulo: "caiu", cor: "var(--abordar)" },
  confianca_caiu: { marca: "◑", rotulo: "perdemos o rastro", cor: "var(--monitorar)" },
  estavel: { marca: "•", rotulo: "estável", cor: "var(--monitorar)" },
};

function frase(a: AxisDelta): string {
  const eixo = AXIS_LABEL[a.axis] ?? a.axis;
  if (a.kind === "nova_evidencia") return `${eixo}: apareceu evidência que não existia antes`;
  if (a.kind === "confianca_caiu") return `${eixo}: a evidência que sustentava o eixo não se repetiu`;
  const sinal = a.score_change > 0 ? `+${a.score_change}` : `${a.score_change}`;
  return `${eixo}: ${sinal} pontos`;
}

/** Uma linha, para o registro da fila. Só a manchete. */
export function DeltaTag({ delta }: { delta?: ScoreDelta | null }) {
  const a = delta?.headline_axis;
  if (!a || !delta?.has_changes) return null;
  const e = ESTILO[a.kind] ?? ESTILO.estavel;
  return (
    <span className="change" style={{ ["--change-fg" as string]: e.cor }}>
      <span aria-hidden>{e.marca}</span>
      {frase(a)}
    </span>
  );
}

/** Seção do perfil: todos os eixos que se moveram desde a execução anterior. */
export function DeltaSection({ delta }: { delta?: ScoreDelta | null }) {
  const moveram = (delta?.axes ?? []).filter((a) => a.kind !== "estavel");
  if (!delta || moveram.length === 0) return null;

  const ordem: ChangeKind[] = ["nova_evidencia", "piorou", "melhorou", "confianca_caiu"];
  moveram.sort((x, y) => ordem.indexOf(x.kind) - ordem.indexOf(y.kind));

  return (
    <section className="section">
      <h2>O que mudou desde a última varredura</h2>
      <p className="lede" style={{ fontSize: 15, marginBottom: 18 }}>
        Sinal que sumiu de uma coleta para a outra aparece como perda de rastro,
        nunca como queda do eixo: uma página fora do ar não é regressão da empresa.
      </p>
      <ul className="change-list">
        {moveram.map((a) => {
          const e = ESTILO[a.kind] ?? ESTILO.estavel;
          return (
            <li key={a.axis}>
              <span className="change" style={{ ["--change-fg" as string]: e.cor }}>
                <span aria-hidden>{e.marca}</span>
                {e.rotulo}
              </span>
              <p className="change-line">{frase(a)}</p>
              <p className="change-nums num">
                score {Math.round(a.score_before)} para {Math.round(a.score_after)}, confiança{" "}
                {a.confidence_before.toFixed(2)} para {a.confidence_after.toFixed(2)}
              </p>
              {a.kind === "nova_evidencia" && a.new_evidences && a.new_evidences.length > 0 ? (
                <div className="evidence" style={{ marginTop: 10 }}>
                  {a.new_evidences.slice(0, 3).map((ev, i) => (
                    <div key={i} style={{ marginTop: i ? 10 : 0 }}>
                      <a href={ev.url} target="_blank" rel="noopener noreferrer">
                        {hostname(ev.url)}
                      </a>
                      <blockquote>
                        “{ev.excerpt.slice(0, 220)}{ev.excerpt.length > 220 ? "…" : ""}”
                      </blockquote>
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

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
