"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AXIS_LABEL, api, type QueueItem } from "@/lib/api";
import { BucketChip } from "@/components/Chip";
import { DeltaTag } from "@/components/Delta";
import { Reading } from "@/components/Reading";

const EIXOS = [
  ["Dados proprietários", 30, "Tem dado que um laboratório não consegue comprar?"],
  ["Profundidade de workflow", 25, "Chat genérico, ou escreve dentro do ERP do cliente?"],
  ["Domínio da stack", 25, "Controla custo e latência, ou repassa API de terceiro?"],
  ["Distribuição", 20, "Canal que um anúncio de feature não replica?"],
] as const;

/**
 * Painel — abre na leitura, não numa explicação.
 *
 * A coisa mais característica deste produto é a fila em si: um nome de empresa
 * com o risco medido ao lado e o motivo de a conversa ser agora. Então é isso
 * que aparece primeiro, ao vivo. A explicação de como o score funciona vem
 * depois, para quem ainda não confia na ordem.
 */
export default function PainelPage() {
  const [itens, setItens] = useState<QueueItem[] | null>(null);
  const [indisponivel, setIndisponivel] = useState(false);

  useEffect(() => {
    api.fila().then(setItens).catch(() => setIndisponivel(true));
  }, []);

  const topo = itens?.[0];
  const seguintes = itens?.slice(1, 4) ?? [];

  return (
    <div className="stack">
      <div className="stack-sm">
        <h1>Quais startups brasileiras de IA viram feature de keynote no ano que vem</h1>
        <p className="lede">
          O radar varre fontes públicas, mede o quanto cada startup está protegida contra a
          comoditização pelos grandes laboratórios e transforma o diagnóstico numa conversa —
          com a tecnologia NVIDIA que endereça exatamente o ponto fraco dela.
        </p>
      </div>

      <section className="section">
        <h2>Próxima conversa</h2>

        {indisponivel ? (
          <p className="alert" role="alert">
            <strong>A fila não carregou.</strong> A API precisa estar no ar
            (<code>make api</code>) para o painel mostrar o diagnóstico mais recente.
          </p>
        ) : itens === null ? (
          <p className="note">Carregando…</p>
        ) : !topo ? (
          <p className="empty" style={{ borderTop: "none", paddingTop: 8 }}>
            Nada diagnosticado ainda. Descreva um setor e o radar encontra, lê e pontua as
            empresas sozinho.{" "}
            <Link href="/busca" className="link-fwd">Rodar a primeira varredura</Link>.
          </p>
        ) : (
          <>
            <Link href={`/empresa/${topo.company_id}`} style={{ textDecoration: "none" }}>
              <span className="entry-name" style={{ fontSize: 40 }}>{topo.company_name}</span>
            </Link>
            <div className="entry-meta" style={{ marginTop: 8, marginBottom: 20 }}>
              {topo.bucket ? <BucketChip bucket={topo.bucket} big /> : null}
              <span>eixo mais fraco: {AXIS_LABEL[topo.weakest_axis] ?? topo.weakest_axis}</span>
            </div>
            {topo.delta?.has_changes ? (
              <p className="entry-trigger" style={{ marginBottom: 20 }}>
                <DeltaTag delta={topo.delta} />
              </p>
            ) : null}
            <div style={{ maxWidth: 460 }}>
              <Reading
                name="Risco de comoditização"
                value={topo.commoditization_risk}
                confidence={topo.global_confidence}
                suffix="/100"
                weak
                aside={
                  typeof topo.urgency === "number" ? `urgência ${topo.urgency.toFixed(0)}` : undefined
                }
              />
            </div>

            {seguintes.length > 0 ? (
              <div className="register" style={{ marginTop: 28 }}>
                {seguintes.map((it, i) => (
                  <Link key={it.company_id} href={`/empresa/${it.company_id}`} className="entry">
                    <span className="entry-rank num" aria-hidden>{i + 2}</span>
                    <span>
                      <span className="entry-name" style={{ fontSize: 19 }}>{it.company_name}</span>
                      <span className="entry-meta">
                        {it.bucket ? <BucketChip bucket={it.bucket} /> : null}
                        <span className="num">risco {Math.round(it.commoditization_risk)}</span>
                      </span>
                    </span>
                    <span />
                  </Link>
                ))}
              </div>
            ) : null}

            <p style={{ marginTop: 22 }}>
              <Link href="/fila" className="link-fwd">Ver a fila inteira</Link>
            </p>
          </>
        )}
      </section>

      <section className="section">
        <h2>Como o radar decide</h2>
        <p className="lede" style={{ fontSize: 15.5 }}>
          Cada startup recebe um score de defensibilidade de 0 a 100 em quatro eixos
          ponderados. O complemento é o risco de comoditização: quanto dela um lançamento
          da OpenAI ou do Google conseguiria substituir.
        </p>
        <dl className="kv" style={{ marginTop: 20, gap: "16px 24px" }}>
          {EIXOS.map(([nome, peso, pergunta]) => (
            <div key={nome} style={{ display: "contents" }}>
              <dt className="num" style={{ color: "var(--ink)", fontWeight: 600 }}>{peso}%</dt>
              <dd>
                <b style={{ fontWeight: 600 }}>{nome}</b>
                <br />
                <span className="note">{pergunta}</span>
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="section">
        <h2>Duas regras que o sistema não quebra</h2>
        <div style={{ display: "flex", flexDirection: "column", gap: 18, marginTop: 4 }}>
          <p style={{ maxWidth: "64ch" }}>
            <b style={{ fontWeight: 600 }}>Score e confiança são números separados.</b>{" "}
            <span className="note" style={{ fontSize: 16 }}>
              Startup discreta produz pouca evidência. Quando não encontramos sinal, o eixo
              aparece como evidência insuficiente — a barra vira hachura e o número some.
              Nunca vira nota baixa: confundir as duas coisas seria injustiça com aparência
              de rigor.
            </span>
          </p>
          <p style={{ maxWidth: "64ch" }}>
            <b style={{ fontWeight: 600 }}>Nenhuma afirmação existe sem fonte.</b>{" "}
            <span className="note" style={{ fontSize: 16 }}>
              Toda informação vem com link e o trecho literal que a sustenta, clicável na
              tela do perfil. Sem fonte recuperada, o sistema bloqueia a recomendação em vez
              de escrever algo plausível.
            </span>
          </p>
        </div>
      </section>
    </div>
  );
}
