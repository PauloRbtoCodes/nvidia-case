import { AXIS_LABEL, type AxisOut } from "@/lib/api";
import { Reading } from "@/components/Reading";

const PESOS: Record<string, number> = {
  proprietary_data: 0.3,
  workflow_depth: 0.25,
  stack_ownership: 0.25,
  distribution: 0.2,
};

/**
 * Os quatro eixos do Defensibility Radar, cada um como uma leitura com a sua
 * sustentação (ver `Reading`).
 *
 * A regra que esta tela **precisa** honrar (ADR 0002): eixo com confiança
 * abaixo de 0,35 é "evidência insuficiente", nunca nota baixa. Startup discreta
 * produz pouca evidência, e mostrar 40/100 onde o certo é "não sabemos" é
 * injustiça com aparência de rigor.
 *
 * Os eixos saem na ordem do peso, não na ordem que vieram: quem decide olhando
 * a tela precisa ver primeiro o que mais move o score.
 */
export function Axes({ axes, weakest }: { axes: AxisOut[]; weakest?: string | null }) {
  const ordenados = [...axes].sort(
    (a, b) => (PESOS[b.axis] ?? 0) - (PESOS[a.axis] ?? 0),
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 30 }}>
      {ordenados.map((a) => {
        const peso = PESOS[a.axis];
        const fraco = a.axis === weakest;
        return (
          <div key={a.axis}>
            <Reading
              name={AXIS_LABEL[a.axis] ?? a.axis}
              value={a.score}
              confidence={a.confidence}
              suffix="/100"
              aside={peso ? `peso ${Math.round(peso * 100)}% do score` : undefined}
            />
            {fraco ? (
              <p className="note" style={{ marginTop: 6, color: "var(--abordar)" }}>
                Eixo mais fraco — é daqui que a recomendação NVIDIA sai.
              </p>
            ) : null}
            {a.rationale ? (
              <p className="note" style={{ marginTop: 8, maxWidth: "68ch" }}>{a.rationale}</p>
            ) : null}
            {a.evidences && a.evidences.length > 0 ? (
              <div className="evidence" style={{ marginTop: 12 }}>
                {a.evidences.slice(0, 3).map((e, i) => (
                  <div key={i} style={{ marginTop: i ? 12 : 0 }}>
                    <a href={e.url} target="_blank" rel="noopener noreferrer">
                      {hostname(e.url)}
                    </a>
                    <blockquote>“{e.excerpt}”</blockquote>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
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
