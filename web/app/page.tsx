import Link from "next/link";
import { IconBusca, IconFila, IconSeta } from "@/components/Icons";

/**
 * Painel — o destino da logo e a porta de entrada.
 *
 * Existe porque clicar na marca disparava a tela de busca, e "voltar ao início"
 * não deveria levar a uma tela de ação. Além disso é onde o sistema se explica:
 * quem abre pela primeira vez precisa saber o que a fila significa antes de
 * confiar na ordem dela.
 */
export default function PainelPage() {
  return (
    <div className="stack">
      <section className="hero">
        <span className="label">Startups &amp; VCs · Inception Brasil</span>
        <h1>Quais startups brasileiras de IA correm risco de virar feature de keynote</h1>
        <p>
          O radar varre fontes públicas, mede o quanto cada startup está protegida
          contra a comoditização pelos grandes labs e transforma esse diagnóstico numa
          conversa — com a tecnologia NVIDIA que endereça exatamente o ponto fraco dela.
        </p>
      </section>

      <div className="cards2">
        <Link href="/busca" className="action-card">
          <span className="ac-icon"><IconBusca /></span>
          <h3>Buscar startups</h3>
          <p>
            Descreva um setor ou perfil. O radar encontra as empresas, diagnostica cada
            uma e mostra o progresso ao vivo.
          </p>
          <span className="ac-go">Iniciar uma varredura →</span>
        </Link>

        <Link href="/fila" className="action-card">
          <span className="ac-icon"><IconFila /></span>
          <h3>Fila de prioridade</h3>
          <p>
            As empresas já diagnosticadas, ordenadas por quem merece a próxima
            conversa — e com o motivo ao lado.
          </p>
          <span className="ac-go">Ver a quem falar →</span>
        </Link>
      </div>

      <section className="card stack-sm">
        <div className="section-head">
          <h2>Como o radar decide</h2>
        </div>
        <p style={{ color: "var(--ink-muted)", maxWidth: "68ch" }}>
          Cada startup recebe um <strong style={{ color: "var(--ink)" }}>score de
          defensibilidade</strong> de 0 a 100 em quatro eixos. O complemento é o risco de
          comoditização: quanto dela um lançamento da OpenAI ou do Google conseguiria
          substituir.
        </p>
        <div className="steps" style={{ marginTop: 6 }}>
          {[
            ["Dados proprietários", "30%", "Tem dado que o lab não consegue comprar?"],
            ["Profundidade de workflow", "25%", "Chat genérico, ou escreve dentro do ERP do cliente?"],
            ["Domínio da stack", "25%", "Controla custo e latência, ou repassa API de terceiro?"],
            ["Distribuição", "20%", "Canal que um anúncio de feature não replica?"],
          ].map(([nome, peso, pergunta]) => (
            <div className="step" key={nome}>
              <span className="step-n">{peso}</span>
              <b>{nome}</b>
              <p>{pergunta}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="card stack-sm">
        <div className="section-head"><h2>Duas regras que o sistema não quebra</h2></div>
        <div className="cards2">
          <div className="callout">
            <span className="label">Score ≠ confiança</span>
            <p>
              Startup discreta produz pouca evidência. Quando não encontramos sinal, o eixo
              aparece como <em>evidência insuficiente</em> — nunca como nota baixa. Confundir
              as duas coisas seria injustiça com aparência de rigor.
            </p>
          </div>
          <div className="callout">
            <span className="label">Nenhuma afirmação sem fonte</span>
            <p>
              Toda informação sobre uma empresa vem com link e o trecho literal que a
              sustenta, clicável na tela do perfil. Se não houver fonte, o sistema bloqueia
              em vez de escrever algo plausível.
            </p>
          </div>
        </div>
      </section>

      <p style={{ fontSize: 15 }}>
        <Link href="/fila" style={{ color: "var(--accent-ink)", fontWeight: 600,
          display: "inline-flex", alignItems: "center", gap: 8 }}>
          Ver a fila de prioridade <IconSeta />
        </Link>
      </p>
    </div>
  );
}
