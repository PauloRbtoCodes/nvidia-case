"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

interface Evento {
  t: string;
  node: string;
  company: string;
  msg: string;
  erro?: boolean;
}

const EXEMPLOS = [
  "startups brasileiras de IA para saúde",
  "startups de IA para o agronegócio no Brasil",
  "fintechs brasileiras com IA generativa",
  "startups de IA jurídica no Brasil",
];

/** Chave do que sobrevive a uma troca de tela — não o histórico de eventos, só
 * o suficiente para reconectar ao stream, que devolve o histórico sozinho. */
const CHAVE_RUN_ATIVA = "radar:run_ativa";

interface RunAtiva {
  runId: string;
  query: string;
  iniciadaEm: number;
}

/**
 * Cada rótulo que o backend emite (`api/routers/searches.py: ROTULOS`) cai
 * numa dessas 4 fases. É o mesmo agrupamento do roteiro de apresentação —
 * levantar lastro / diagnosticar / decidir — mais a fase 0 de descoberta.
 * Mantido aqui, e não pedido ao backend, porque é puramente de exibição: o
 * grafo não tem "fases", tem nós, e forçar isso no backend acoplaria a API à
 * forma de uma tela específica.
 */
const FASES = [
  { titulo: "Descobrir", nos: ["Planejando as buscas", "Descobrindo empresas"] },
  {
    titulo: "Levantar lastro",
    nos: ["Coletando páginas", "Extraindo perfil", "Auditando evidências"],
  },
  {
    titulo: "Diagnosticar",
    nos: ["Classificando maturidade", "Pontuando defensibilidade", "Comparando com a execução anterior"],
  },
  {
    titulo: "Decidir",
    nos: ["Buscando na base NVIDIA", "Redigindo recomendações", "Escrevendo o briefing"],
  },
] as const;

/** Passos comuns o bastante para dar um número a quem pergunta "quanto falta". */
const DURACAO_TIPICA_SEGUNDOS: Record<string, number> = {
  "Redigindo recomendações": 60,
  "Buscando na base NVIDIA": 15,
  "Coletando páginas": 20,
};

function faseDoNo(node: string): number {
  const i = FASES.findIndex((f) => (f.nos as readonly string[]).includes(node));
  return i === -1 ? 0 : i;
}

function formatarDuracao(segundos: number): string {
  if (segundos < 60) return `${Math.round(segundos)}s`;
  const min = Math.floor(segundos / 60);
  const seg = Math.round(segundos % 60);
  return `${min}m${seg.toString().padStart(2, "0")}s`;
}

/**
 * Varredura — o formulário e o log ao vivo.
 *
 * A execução sobrevive a trocar de tela por dois mecanismos que precisam
 * funcionar juntos: o backend guarda o histórico de eventos por execução (ver
 * `SearchRun.subscribe`), e esta tela guarda em `localStorage` só o ponteiro
 * (id da execução) para reconectar e puxar esse histórico de volta. Nenhum dos
 * dois sozinho resolve — sem o primeiro, reconectar mostraria a tela vazia;
 * sem o segundo, sair da rota perde a referência ao id.
 */
export default function BuscaPage() {
  const [query, setQuery] = useState("");
  const [maxCompanies, setMax] = useState(5);
  const [runId, setRunId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [eventos, setEventos] = useState<Evento[]>([]);
  const [erro, setErro] = useState<string | null>(null);
  const [rodando, setRodando] = useState(false);
  const [iniciadaEm, setIniciadaEm] = useState<number | null>(null);
  const [agora, setAgora] = useState<number>(() => Date.now());
  const esRef = useRef<EventSource | null>(null);
  const fimRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const ultimoEventoEmRef = useRef<number>(Date.now());

  useEffect(() => () => esRef.current?.close(), []);
  useEffect(() => { fimRef.current?.scrollIntoView({ block: "nearest" }); }, [eventos.length]);

  // Cronômetro visível: 1 tick/segundo é o suficiente para "isso ainda tá
  // rodando" sem gastar re-render à toa.
  useEffect(() => {
    if (!rodando) return;
    const id = setInterval(() => setAgora(Date.now()), 1000);
    return () => clearInterval(id);
  }, [rodando]);

  const salvarRunAtiva = useCallback((ra: RunAtiva | null) => {
    try {
      if (ra) localStorage.setItem(CHAVE_RUN_ATIVA, JSON.stringify(ra));
      else localStorage.removeItem(CHAVE_RUN_ATIVA);
    } catch {
      /* localStorage indisponível (aba privada, cota) — a tela ainda funciona,
         só não sobrevive a uma troca de tela */
    }
  }, []);

  const abrirStream = useCallback((id: string) => {
    esRef.current?.close();
    const es = new EventSource(`/api/searches/${id}/stream`);
    esRef.current = es;

    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        const node = String(d.node ?? d.event ?? d.type ?? "evento");
        const company = String(d.company ?? "");
        const msg = String(d.detail ?? "");
        const erroEvento = d.type === "error" || /fail|erro|error/i.test(node);
        ultimoEventoEmRef.current = Date.now();
        setEventos((prev) => [
          ...prev,
          { t: new Date().toLocaleTimeString("pt-BR", { hour12: false }), node, company, msg, erro: erroEvento },
        ]);
        if (d.type === "status" && d.detail) setStatus(String(d.detail));
        if (d.detail === "concluida" || d.detail === "falhou") {
          setRodando(false);
          salvarRunAtiva(null);
          es.close();
        }
      } catch {
        /* keep-alive do SSE não é JSON — ignorar é o comportamento correto */
      }
    };

    es.onerror = () => {
      // A conexão cai por rede instável mesmo com a execução viva no backend —
      // o histórico está seguro lá (`SearchRun.events`), então a resposta certa
      // é tentar de novo, não desistir e mostrar erro para o usuário.
      es.close();
      esRef.current = null;
      window.setTimeout(() => {
        if (document.visibilityState === "visible") abrirStream(id);
      }, 2000);
    };
  }, [salvarRunAtiva]);

  // Reconecta a uma execução em andamento ao montar a tela — é o que resolve
  // "se eu troco de tela o processo morre": o processo nunca morreu no
  // backend, só a tela tinha perdido a referência para ele.
  useEffect(() => {
    let salva: RunAtiva | null = null;
    try {
      const bruto = localStorage.getItem(CHAVE_RUN_ATIVA);
      salva = bruto ? (JSON.parse(bruto) as RunAtiva) : null;
    } catch {
      salva = null;
    }
    if (!salva) return;

    api
      .fila()
      .catch(() => null)
      .finally(async () => {
        try {
          const status = await fetch(`/api/searches/${salva!.runId}`, { cache: "no-store" });
          if (!status.ok) {
            salvarRunAtiva(null);
            return;
          }
          const dados = await status.json();
          setRunId(salva!.runId);
          setQuery(salva!.query);
          setIniciadaEm(salva!.iniciadaEm);
          setStatus(dados.status ?? "executando");
          const aindaRodando = dados.status === "executando" || dados.status === "pendente";
          setRodando(aindaRodando);
          setEventos([]);
          abrirStream(salva!.runId);
          if (!aindaRodando) salvarRunAtiva(null);
        } catch {
          salvarRunAtiva(null);
        }
      });
    // Intencional: só na montagem. `abrirStream` e `salvarRunAtiva` são
    // estáveis (useCallback) e reexecutar isto a cada render reconectaria em
    // loop.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function iniciar(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setErro(null);
    setEventos([]);
    setRodando(true);
    setStatus("iniciando");
    const inicio = Date.now();
    setIniciadaEm(inicio);
    try {
      const run = await api.buscar(query, maxCompanies);
      setRunId(run.id);
      setStatus(run.status ?? "em_execucao");
      salvarRunAtiva({ runId: run.id, query, iniciadaEm: inicio });
      ultimoEventoEmRef.current = Date.now();
      abrirStream(run.id);
    } catch (err) {
      setErro(err instanceof Error ? err.message : String(err));
      setRodando(false);
      setIniciadaEm(null);
    }
  }

  async function cancelar() {
    if (!runId) return;
    try {
      await api.cancelarBusca(runId);
    } catch {
      /* o backend pode já ter terminado entre o clique e a chamada — tanto
         faz, o efeito desejado (parar de esperar por ela) já acontece abaixo */
    }
    setRodando(false);
    salvarRunAtiva(null);
    esRef.current?.close();
  }

  const ultimoEvento = eventos.length > 0 ? eventos[eventos.length - 1] : null;
  const faseAtual = ultimoEvento ? faseDoNo(ultimoEvento.node) : -1;
  const segundosDecorridos = iniciadaEm ? (agora - iniciadaEm) / 1000 : 0;
  const segundosSemEvento = (agora - ultimoEventoEmRef.current) / 1000;
  // "Travado" é relativo ao passo: redigir recomendação leva ~1min de verdade
  // (uma chamada de raciocínio contra a base inteira), então o aviso de
  // demora observa a duração típica daquele passo específico, não um número
  // fixo — senão o aviso "ainda trabalhando" dispara toda vez nesse passo.
  const duracaoTipica = ultimoEvento ? DURACAO_TIPICA_SEGUNDOS[ultimoEvento.node] ?? 8 : 8;
  const pareceTravado = rodando && segundosSemEvento > duracaoTipica * 1.8;

  return (
    <div className="stack">
      <div className="stack-sm">
        <h1>Descreva o setor que você quer varrer</h1>
        <p className="lede">
          Escreva como falaria com um colega — setor, estágio, região. O radar traduz isso
          em buscas reais, encontra as empresas e diagnostica cada uma, com o progresso
          aparecendo aqui enquanto acontece — mesmo se você sair e voltar a esta tela.
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
              disabled={rodando}
            />
          </div>
          <div className="field is-narrow">
            <label htmlFor="n">Quantas empresas</label>
            <input
              id="n" type="number" min={1} max={12} value={maxCompanies}
              onChange={(e) => setMax(Number(e.target.value))}
              disabled={rodando}
            />
          </div>
          <button className="btn" type="submit" disabled={rodando}>
            {rodando ? "Varrendo…" : "Iniciar varredura"}
          </button>
        </div>

        <div className="chips" style={{ marginTop: 4 }}>
          {EXEMPLOS.map((ex) => (
            <button key={ex} type="button" className="chip-btn" disabled={rodando}
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
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap", alignItems: "baseline" }}>
            <h2>Progresso</h2>
            <span className="note num" style={{ display: "flex", gap: 14, alignItems: "baseline" }}>
              {iniciadaEm ? <span>{formatarDuracao(segundosDecorridos)}</span> : null}
              <StatusBadge status={status} />
              {rodando ? (
                <button type="button" className="chip-btn" onClick={cancelar}>Cancelar</button>
              ) : null}
            </span>
          </div>

          {rodando ? <TrilhaDeFases faseAtual={faseAtual} /> : null}

          {rodando && pareceTravado ? (
            <p className="note" style={{ marginTop: 10, fontStyle: "italic" }}>
              Ainda trabalhando em <b>{ultimoEvento?.node.toLowerCase()}</b>
              {ultimoEvento?.company ? <> ({ultimoEvento.company})</> : null} — esse passo
              costuma levar até {formatarDuracao(duracaoTipica)}. Sem erro no log, é chamada de
              modelo em andamento, não travamento.
            </p>
          ) : null}

          {eventos.length === 0 ? (
            <p className="note" style={{ marginTop: 14 }}>Aguardando o primeiro passo do radar…</p>
          ) : (
            <div className="log" style={{ marginTop: 14 }}>
              {eventos.map((ev, i) => (
                <div className={`log-line${ev.erro ? " is-error" : ""}`} key={i}>
                  <time>{ev.t}</time>
                  <b>{ev.node}{ev.company ? ` · ${ev.company}` : ""}</b>
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
            {FASES.map((fase, i) => (
              <div key={fase.titulo} style={{ display: "contents" }}>
                <dt className="num" style={{ color: "var(--ink-soft)" }}>{i + 1}</dt>
                <dd>
                  <b style={{ fontWeight: 600 }}>{fase.titulo}</b>
                  <br />
                  <span className="note">{fase.nos.join(" · ")}</span>
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const rotulo: Record<string, string> = {
    executando: "rodando",
    pendente: "na fila",
    concluida: "concluída",
    falhou: "falhou",
    iniciando: "iniciando",
  };
  const cor: Record<string, string> = {
    executando: "var(--green-ink)",
    pendente: "var(--ink-soft)",
    concluida: "var(--green-ink)",
    falhou: "var(--abordar)",
    iniciando: "var(--ink-soft)",
  };
  return (
    <span style={{ color: cor[status] ?? "var(--ink-soft)", fontWeight: 600 }}>
      {status === "executando" ? <PulsoAoVivo /> : null}
      {rotulo[status] ?? status}
    </span>
  );
}

function PulsoAoVivo() {
  return (
    <span
      aria-hidden
      style={{
        display: "inline-block", width: 7, height: 7, borderRadius: "50%",
        background: "var(--green-ink)", marginRight: 6, animation: "radar-pulso 1.4s ease-in-out infinite",
      }}
    />
  );
}

function TrilhaDeFases({ faseAtual }: { faseAtual: number }) {
  return (
    <div style={{ display: "flex", gap: 6, marginTop: 16 }} role="progressbar"
         aria-valuemin={0} aria-valuemax={FASES.length - 1} aria-valuenow={Math.max(faseAtual, 0)}>
      {FASES.map((fase, i) => {
        const concluida = i < faseAtual;
        const atual = i === faseAtual;
        return (
          <div key={fase.titulo} style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
            <div
              style={{
                height: 5, borderRadius: 3,
                background: concluida || atual ? "var(--green-ink)" : "var(--track)",
                opacity: atual ? 1 : concluida ? 0.7 : 1,
                animation: atual ? "radar-pulso-trilha 1.6s ease-in-out infinite" : undefined,
              }}
            />
            <span className="note" style={{ fontSize: 12.5, fontWeight: atual ? 600 : 400, color: atual ? "var(--ink)" : undefined }}>
              {fase.titulo}
            </span>
          </div>
        );
      })}
    </div>
  );
}
