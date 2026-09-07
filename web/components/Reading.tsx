import { CONFIANCA_MINIMA } from "@/lib/api";

/**
 * A marca central da interface: um valor e a sua sustentação, na mesma escala.
 *
 * A barra grossa é a leitura; a fina logo abaixo é quanta evidência a sustenta.
 * Ler as duas juntas é o que impede o erro que o ADR 0002 existe para evitar —
 * confundir "pontuou baixo" com "não apuramos". Abaixo do corte de confiança o
 * número desaparece e a barra vira hachura aberta na régua inteira: o sistema
 * não tem direito de afirmar um valor que não sustenta, e uma barra curta
 * mentiria dizendo "medimos, e deu pouco".
 */
export function Reading({
  name,
  value,
  confidence,
  suffix,
  weak = false,
  aside,
  decimals = 0,
}: {
  name: string;
  value: number;
  confidence: number;
  /** Unidade após o número, ex.: "/100". */
  suffix?: string;
  /** Eixo mais fraco — é dele que sai a recomendação. */
  weak?: boolean;
  /** Linha à direita da legenda. */
  aside?: string;
  decimals?: number;
}) {
  const insuficiente = confidence < CONFIANCA_MINIMA;
  const largura = Math.max(2, Math.min(100, value));

  return (
    <div className="reading">
      <div className="reading-head">
        <span className="reading-name">{name}</span>
        {insuficiente ? (
          <span className="reading-unknown">evidência insuficiente</span>
        ) : (
          <span className="reading-value num">
            {value.toFixed(decimals)}
            {suffix ? <small>{suffix}</small> : null}
          </span>
        )}
      </div>

      <div
        className="gauge"
        role="img"
        aria-label={
          insuficiente
            ? `${name}: evidência insuficiente para pontuar, confiança ${confidence.toFixed(2)}`
            : `${name}: ${value.toFixed(decimals)}${suffix ?? ""}, sustentado por confiança ${confidence.toFixed(2)}`
        }
      >
        <div className="gauge-track">
          <div
            className={`gauge-fill${weak && !insuficiente ? " is-weak" : ""}${insuficiente ? " is-unknown" : ""}`}
            style={{ width: insuficiente ? "100%" : `${largura}%` }}
          />
        </div>
        <div className="gauge-track is-thin">
          <div className="gauge-fill" style={{ width: `${Math.max(1, confidence * 100)}%` }} />
        </div>
      </div>

      <p className="gauge-caption">
        <span>confiança {confidence.toFixed(2)}</span>
        {aside ? <span>{aside}</span> : null}
      </p>
    </div>
  );
}

/**
 * Versão de uma linha, para a fila. Mesma gramática, sem a legenda: numa lista
 * de vinte empresas a legenda repetida vira ruído, e a barra fina já diz o que
 * ela diria.
 */
export function MiniReading({
  label,
  value,
  confidence,
  weak = false,
}: {
  label: string;
  value: number;
  confidence: number;
  weak?: boolean;
}) {
  const insuficiente = confidence < CONFIANCA_MINIMA;
  return (
    <div
      className="reading"
      role="img"
      aria-label={
        insuficiente
          ? `${label}: evidência insuficiente, confiança ${confidence.toFixed(2)}`
          : `${label} ${Math.round(value)} de 100, confiança ${confidence.toFixed(2)}`
      }
    >
      <div className="reading-head" style={{ marginBottom: 5, alignItems: "baseline" }}>
        <span className="note" style={{ lineHeight: 1.2 }}>{label}</span>
        {insuficiente ? (
          <span className="reading-unknown" style={{ fontSize: 13, flex: "none" }}>sem base</span>
        ) : (
          <span className="num" style={{ fontWeight: 600, fontSize: 17, flex: "none" }}>
            {Math.round(value)}
          </span>
        )}
      </div>
      <div className="gauge" aria-hidden>
        <div className="gauge-track">
          <div
            className={`gauge-fill${weak && !insuficiente ? " is-weak" : ""}${insuficiente ? " is-unknown" : ""}`}
            style={{ width: insuficiente ? "100%" : `${Math.max(2, Math.min(100, value))}%` }}
          />
        </div>
        <div className="gauge-track is-thin">
          <div className="gauge-fill" style={{ width: `${Math.max(1, confidence * 100)}%` }} />
        </div>
      </div>
    </div>
  );
}
