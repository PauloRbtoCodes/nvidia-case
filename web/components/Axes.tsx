import { AXIS_LABEL, CONFIANCA_MINIMA, type AxisOut } from "@/lib/api";

const PESOS: Record<string, number> = {
  proprietary_data: 0.3,
  workflow_depth: 0.25,
  stack_ownership: 0.25,
  distribution: 0.2,
};

/**
 * O Defensibility Radar em quatro barras.
 *
 * A regra que esta tela **precisa** honrar (ADR 0002): eixo com confiança abaixo
 * de 0,35 é renderizado como "evidência insuficiente", nunca como nota baixa.
 * Startup discreta produz pouca evidência, e mostrar 40/100 onde o certo é "não
 * sabemos" é injustiça com aparência de rigor — a barra hachurada existe para
 * que a diferença seja visível sem ler o texto.
 */
export function Axes({ axes, weakest }: { axes: AxisOut[]; weakest?: string | null }) {
  return (
    <div className="axes">
      {axes.map((a) => {
        const insuficiente = a.confidence < CONFIANCA_MINIMA;
        const fraco = !insuficiente && a.axis === weakest;
        const peso = PESOS[a.axis];
        return (
          <div key={a.axis}>
            <div className="axis-head">
              <span className="axis-name">
                {AXIS_LABEL[a.axis] ?? a.axis}
                {peso ? <span className="axis-weight"> · peso {Math.round(peso * 100)}%</span> : null}
              </span>
              {insuficiente ? (
                <span className="axis-insufficient">evidência insuficiente</span>
              ) : (
                <span className="axis-value">
                  {Math.round(a.score)}<span style={{ color: "var(--ink-muted)", fontWeight: 400 }}>/100</span>
                </span>
              )}
            </div>
            <div
              className="bar"
              role="img"
              aria-label={
                insuficiente
                  ? `${AXIS_LABEL[a.axis] ?? a.axis}: evidência insuficiente para pontuar`
                  : `${AXIS_LABEL[a.axis] ?? a.axis}: ${Math.round(a.score)} de 100, confiança ${a.confidence.toFixed(2)}`
              }
            >
              <div
                className={`bar-fill${fraco ? " is-weak" : ""}${insuficiente ? " is-unknown" : ""}`}
                style={{ width: insuficiente ? "100%" : `${Math.max(2, Math.min(100, a.score))}%` }}
              />
            </div>
            <p className="axis-conf">
              Confiança {a.confidence.toFixed(2)}
              {fraco ? " · eixo mais fraco, é daqui que sai a recomendação" : ""}
            </p>
            {a.rationale ? (
              <p className="axis-conf" style={{ marginTop: 4 }}>{a.rationale}</p>
            ) : null}
            {a.evidences && a.evidences.length > 0 ? (
              <div className="evidence" style={{ marginTop: 10 }}>
                {a.evidences.slice(0, 3).map((e, i) => (
                  <div key={i} style={{ marginTop: i ? 10 : 0 }}>
                    <a href={e.url} target="_blank" rel="noopener noreferrer">
                      {new URL(e.url).hostname.replace(/^www\./, "")}
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
