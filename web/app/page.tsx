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

/**
 * Tela 1 — busca com progresso ao vivo.
 *
 * O POST devolve 202 e um id; o progresso vem por SSE. A ordem importa e é o
 * motivo de `SearchRun.subscribe` entregar histórico antes dos eventos vivos:
 * abrimos o stream **depois** do POST, e sem histórico os eventos emitidos
 * nesse intervalo sumiriam — a tela abriria com o progresso pela metade.
 */
export default function BuscaPage() {
  const [query, setQuery] = useState("startups brasileiras de IA para saúde");
  const [maxCompanies, setMax] = useState(3);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [rodando, setRodando] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const fimRef = useRef<HTMLDivElement | null>(null);

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

    es.onerror = () => {
      // O EventSource reconecta sozinho; só encerramos quando a execução acabou.
      if (!rodando) es.close();
    };
  }, [rodando]);

  async function iniciar(e: React.FormEvent) {
    e.preventDefault();
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
        <span className="label">Nova varredura</span>
        <h1>Quem procurar nesta semana</h1>
        <p style={{ color: "var(--ink-muted)", maxWidth: "62ch" }}>
          O radar varre fontes públicas, mede o quanto cada startup está exposta à
          comoditização pelos grandes labs e ordena a fila por{" "}
          <strong style={{ color: "var(--ink)" }}>risco × capacidade de agir</strong>.
        </p>
      </div>

      <form className="card search-form" onSubmit={iniciar}>
        <div className="field">
          <label htmlFor="q">Consulta</label>
          <input
            id="q"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="startups brasileiras de IA para saúde"
            required
          />
        </div>
        <div className="field narrow">
          <label htmlFor="n">Empresas</label>
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
      </form>

      {erro ? (
        <p className="alert" role="alert">
          <strong>Não foi possível iniciar.</strong> {erro}
          <br />
          Confira se a API está no ar em <code className="mono">localhost:8000</code> (
          <code className="mono">make api</code>).
        </p>
      ) : null}

      {runId ? (
        <section className="card stack-sm" aria-live="polite">
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <h2>Progresso</h2>
            <span className="mono" style={{ fontSize: 13, color: "var(--ink-muted)" }}>
              {status} · {eventos.length} evento{eventos.length === 1 ? "" : "s"}
            </span>
          </div>
          {eventos.length === 0 ? (
            <p style={{ color: "var(--ink-muted)", fontSize: 14 }}>
              Aguardando o primeiro evento do grafo…
            </p>
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
            <p style={{ fontSize: 14 }}>
              <Link href="/fila" style={{ color: "var(--accent)", fontWeight: 600 }}>
                Ver a fila de prioridade →
              </Link>
            </p>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}
