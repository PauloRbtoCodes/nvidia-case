"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { IconSeta } from "@/components/Icons";

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

const PASSOS = [
  ["1", "Planeja a busca", "Um modelo traduz sua consulta em consultas de pesquisa reais"],
  ["2", "Descobre empresas", "Filtra notícias, vagas e diretórios — só sites de empresa passam"],
  ["3", "Coleta e classifica", "Lê o site, as vagas e a imprensa; decide se é AI-native"],
  ["4", "Pontua e recomenda", "Mede defensibilidade e sugere a tecnologia NVIDIA certa"],
] as const;

/**
 * Tela de busca — com a explicação do pipeline antes do formulário.
 *
 * Sem contexto, "iniciar varredura" é um botão sem promessa clara: quanto
 * tempo leva, o que aparece, por que confiar no resultado. Os quatro passos
 * abaixo espelham exatamente os nós do grafo (plan → discover → collect/
 * classify → score/recommend), então a explicação nunca diverge do que roda.
 */
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
        const msg = String(d.message ?? d.detail ?? d.company ?? JSON.stringify(d));
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

  function usarExemplo(texto: string) {
    setQuery(texto);
    inputRef.current?.focus();
  }

  return (
    <div className="stack">
      <div className="stack-sm">
        <span className="label">Nova varredura</span>
        <h1>Descreva o setor ou perfil que você procura</h1>
        <p style={{ color: "var(--ink-muted)", maxWidth: "64ch" }}>
          Escreva como falaria com um colega — setor, estágio, região. O radar
          traduz isso em buscas reais, encontra as empresas e diagnostica cada
          uma, com o progresso aparecendo aqui ao vivo.
        </p>
      </div>

      <section className="card stack-sm">
        <span className="label">Como funciona</span>
        <div className="steps">
          {PASSOS.map(([n, titulo, desc]) => (
            <div className="step" key={n}>
              <span className="step-n">{n}</span>
              <b>{titulo}</b>
              <p>{desc}</p>
            </div>
          ))}
        </div>
      </section>

      <form className="card stack-sm" onSubmit={iniciar}>
        <div className="search-form">
          <div className="field">
            <label htmlFor="q">O que você procura</label>
            <input
              ref={inputRef}
              id="q"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="ex.: startups brasileiras de IA para saúde"
              required
            />
          </div>
          <div className="field narrow">
            <label htmlFor="n">Quantas empresas</label>
            <input
              id="n"
              type="number"
              min={1}
              max={12}
              value={maxCompanies}
              onChange={(e) => setMax(Number(e.target.value))}
            />
          </div>
          <button className="btn" type="submit" disabled={rodando}>
            {rodando ? "Executando…" : "Iniciar varredura"}
          </button>
        </div>

        <div className="stack-sm" style={{ gap: 8 }}>
          <span className="label" style={{ fontSize: 11 }}>Ou experimente um exemplo</span>
          <div className="examples">
            {EXEMPLOS.map((ex) => (
              <button key={ex} type="button" className="example" onClick={() => usarExemplo(ex)}>
                {ex}
              </button>
            ))}
          </div>
        </div>
      </form>

      {erro ? (
        <p className="alert" role="alert">
          <strong>Não foi possível iniciar a varredura.</strong> {erro}
          <br />
          Confira se a API está no ar em <code className="mono">localhost:8000</code>{" "}
          (comando <code className="mono">make api</code>).
        </p>
      ) : null}

      {runId ? (
        <section className="card stack-sm" aria-live="polite">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <h2>Progresso da varredura</h2>
            <span className="mono" style={{ fontSize: 14, color: "var(--ink-muted)" }}>
              {status} · {eventos.length} evento{eventos.length === 1 ? "" : "s"}
            </span>
          </div>
          {eventos.length === 0 ? (
            <p style={{ color: "var(--ink-muted)" }}>Aguardando o primeiro passo do radar…</p>
          ) : (
            <div className="events">
              {eventos.map((ev, i) => (
                <div className={`event${ev.erro ? " is-error" : ""}`} key={i}>
                  <time>{ev.t}</time>
                  <span className="node">{ev.node}</span>
                  <p>{ev.msg}</p>
                </div>
              ))}
              <div ref={fimRef} />
            </div>
          )}
          {!rodando && eventos.length > 0 ? (
            <div className="callout" style={{ marginTop: 4 }}>
              <span className="label">Varredura concluída</span>
              <p>
                <Link href="/fila" style={{ color: "var(--accent-ink)", fontWeight: 600,
                  display: "inline-flex", alignItems: "center", gap: 8 }}>
                  Ver quem entrou na fila de prioridade <IconSeta />
                </Link>
              </p>
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
