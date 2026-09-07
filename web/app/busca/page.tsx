"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface Evento {
  t: string;
  node: string;
  msg: string;
  erro?: boolean;
}

const EXEMPLOS = [
  "startups brasileiras de IA para saúde",
  "startups de IA para o agronegócio no Brasil",
  "fintechs brasileiras com IA generativa",
  "startups de IA jurídica no Brasil",
];

/**
 * Varredura — o formulário e o log ao vivo.
 *
 * A numeração dos passos é legítima aqui: eles são a ordem real em que o grafo
 * executa (plan → discover → collect/classify → score/recommend), então a
 * explicação nunca diverge do que roda. É o único outro lugar do produto, além
 * da fila, onde o conteúdo é mesmo uma sequência.
 */
const PASSOS = [
  ["Planeja a busca", "Um modelo traduz sua frase em consultas de pesquisa reais"],
  ["Descobre empresas", "Filtra notícia, vaga e diretório — só site de empresa passa"],
  ["Lê e classifica", "Percorre site, vagas e imprensa; decide se é AI-native"],
  ["Pontua e recomenda", "Mede defensibilidade e escolhe a tecnologia NVIDIA certa"],
] as const;

export default function BuscaPage() {
  const [query, setQuery] = useState("");
  const [maxCompanies, setMax] = useState(5);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [rodando, setRodando] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const fimRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => () => esRef.current?.close(), []);
  useEffect(() => { fimRef.current?.scrollIntoView({ block: "nearest" }); }, [eventos.length]);

  const abrirStream = useCallback((id: string) => {
    esRef.current?.close();
    const es = new EventSource(`/api/searches/${id}/stream`);
    esRef.current = es;

    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        const node = String(d.node ?? d.event ?? d.type ?? "evento");
        const msg = String(d.message ?? d.detail ?? d.company ?? "");
        const erroEvento = /fail|erro|error/i.test(node) || d.level === "error" || d.kind === "bug";
        setEventos((prev) => [
          ...prev,
          { t: new Date().toLocaleTimeString("pt-BR", { hour12: false }), node, msg, erro: erroEvento },
        ]);
        if (d.status) setStatus(String(d.status));
        if (d.status === "concluida" || d.status === "completed" || d.status === "falhou") {
          setRodando(false);
          es.close();
        }
      } catch {
        /* keep-alive do SSE não é JSON — ignorar é o comportamento correto */
      }
    };

    es.onerror = () => { if (!rodando) es.close(); };
  }, [rodando]);

  async function iniciar(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setErro(null);
    setEventos([]);
    setRodando(true);
    setStatus("iniciando");
    try {
      const run = await api.buscar(query, maxCompanies);
      setRunId(run.id);
      setStatus(run.status ?? "em_execucao");
      abrirStream(run.id);
    } catch (err) {
      setErro(err instanceof Error ? err.message : String(err));
      setRodando(false);
    }
  }

  return (
    <div className="stack">
      <div className="stack-sm">
        <h1>Descreva o setor que você quer varrer</h1>
        <p className="lede">
          Escreva como falaria com um colega — setor, estágio, região. O radar traduz isso
          em buscas reais, encontra as empresas e diagnostica cada uma, com o progresso
          aparecendo aqui enquanto acontece.
        </p>
      </div>

      <form className="sheet stack-sm" onSubmit={iniciar}>
        <div className="form-row">
          <div className="field">
            <label htmlFor="q">O que você procura</label>
            <input
              ref={inputRef}
              id="q"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="startups brasileiras de IA para saúde"
              required
            />
          </div>
          <div className="field is-narrow">
            <label htmlFor="n">Quantas empresas</label>
            <input
              id="n" type="number" min={1} max={12} value={maxCompanies}
              onChange={(e) => setMax(Number(e.target.value))}
            />
          </div>
          <button className="btn" type="submit" disabled={rodando}>
            {rodando ? "Varrendo…" : "Iniciar varredura"}
          </button>
        </div>

        <div className="chips" style={{ marginTop: 4 }}>
          {EXEMPLOS.map((ex) => (
            <button key={ex} type="button" className="chip-btn"
                    onClick={() => { setQuery(ex); inputRef.current?.focus(); }}>
              {ex}
            </button>
          ))}
        </div>
      </form>

      {erro ? (
        <p className="alert" role="alert">
          <strong>A varredura não começou.</strong> {erro} Confira se a API está no ar em{" "}
          <code>localhost:8000</code> — o comando é <code>make api</code>.
        </p>
      ) : null}

      {runId ? (
        <section className="section" aria-live="polite">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <h2>Progresso</h2>
            <span className="note num">
              {status}, {eventos.length} {eventos.length === 1 ? "passo" : "passos"}
            </span>
          </div>
          {eventos.length === 0 ? (
            <p className="note" style={{ marginTop: 14 }}>Aguardando o primeiro passo do radar…</p>
          ) : (
            <div className="log" style={{ marginTop: 14 }}>
              {eventos.map((ev, i) => (
                <div className={`log-line${ev.erro ? " is-error" : ""}`} key={i}>
                  <time>{ev.t}</time>
                  <b>{ev.node}</b>
                  {ev.msg ? <p>{ev.msg}</p> : null}
                </div>
              ))}
              <div ref={fimRef} />
            </div>
          )}
          {!rodando && eventos.length > 0 ? (
            <p style={{ marginTop: 20 }}>
              <Link href="/fila" className="link-fwd">Ver quem entrou na fila</Link>
            </p>
          ) : null}
        </section>
      ) : (
        <section className="section">
          <h2>O que acontece quando você aperta o botão</h2>
          <dl className="kv" style={{ marginTop: 16, gap: "16px 24px" }}>
            {PASSOS.map(([titulo, desc], i) => (
              <div key={titulo} style={{ display: "contents" }}>
                <dt className="num" style={{ color: "var(--ink-soft)" }}>{i + 1}</dt>
                <dd>
                  <b style={{ fontWeight: 600 }}>{titulo}</b>
                  <br />
                  <span className="note">{desc}</span>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}
