/**
 * Cliente da API do Radar.
 *
 * Tudo passa por `/api/...`, que o `next.config.mjs` reescreve para o backend.
 * O front nunca conhece o host da API: trocar o backend é variável de ambiente,
 * não recompilação do cliente.
 */

export type Bucket = "abordar_agora" | "nutrir" | "case_potencial" | "monitorar";

export const BUCKET_LABEL: Record<Bucket, string> = {
  abordar_agora: "Abordar agora",
  nutrir: "Nutrir",
  case_potencial: "Case potencial",
  monitorar: "Monitorar",
};

/** Cada bucket tem cor própria E rótulo — a cor nunca carrega o sentido sozinha. */
export const BUCKET_VARS: Record<Bucket, { fg: string; wash: string }> = {
  abordar_agora: { fg: "var(--abordar)", wash: "var(--abordar-wash)" },
  nutrir: { fg: "var(--nutrir)", wash: "var(--nutrir-wash)" },
  case_potencial: { fg: "var(--case)", wash: "var(--case-wash)" },
  monitorar: { fg: "var(--monitorar)", wash: "var(--monitorar-wash)" },
};

export const AXIS_LABEL: Record<string, string> = {
  proprietary_data: "Dados proprietários",
  workflow_depth: "Profundidade de workflow",
  stack_ownership: "Domínio da stack",
  distribution: "Distribuição",
};

/** Espelha `ACTIONABLE_CONFIDENCE_THRESHOLD` de `models/scoring.py`. */
export const CONFIANCA_MINIMA = 0.35;

/** O que mudou num eixo entre a última execução e a anterior. */
export type ChangeKind =
  | "nova_evidencia"
  | "melhorou"
  | "piorou"
  | "confianca_caiu"
  | "estavel";

export interface AxisDelta {
  axis: string;
  kind: ChangeKind;
  score_before: number;
  score_after: number;
  confidence_before: number;
  confidence_after: number;
  score_change: number;
  is_actionable: boolean;
  new_evidences?: EvidenceOut[];
}

/**
 * O gatilho temporal: o diff entre as duas execuções mais recentes da empresa.
 * Ausência de evidência nunca vira "piorou" — o backend garante o invariante;
 * o front só precisa desenhar `headline_axis`.
 */
export interface ScoreDelta {
  company_name: string;
  weights_version_before: string;
  weights_version_after: string;
  axes: AxisDelta[];
  has_changes: boolean;
  headline_axis?: AxisDelta | null;
}

export interface QueueItem {
  company_id: string;
  company_name: string;
  bucket: Bucket;
  urgency: number;
  commoditization_risk: number;
  global_confidence: number;
  weakest_axis?: string | null;
  sector?: string | null;
  maturity?: string | null;
  recommended_next_step?: string | null;
  delta?: ScoreDelta | null;
}

export interface EvidenceOut {
  url: string;
  excerpt: string;
  kind?: string | null;
  published_at?: string | null;
}

export interface AxisOut {
  axis: string;
  score: number;
  confidence: number;
  rationale?: string | null;
  is_actionable?: boolean;
  evidences?: EvidenceOut[];
}

export interface EvidenceBacked<T> {
  value: T;
  evidences?: EvidenceOut[];
  reasoning?: string | null;
}

export interface Profile {
  name: string;
  website?: string | null;
  description?: string | null;
  sector?: EvidenceBacked<string> | null;
  stage?: string | null;
  open_engineering_roles?: string[];
  source_urls?: string[];
}

export interface Recommendation {
  technology: string;
  addresses_axis: string;
  technical_rationale: string;
  business_rationale: string;
  priority: string;
  complexity: string;
  next_action: string;
  kb_citations?: { text: string; source_url: string; source_title?: string | null }[];
  company_evidences?: EvidenceOut[];
}

/**
 * A resposta é aninhada, não plana: `profile`, `classification`, `defensibility`,
 * `priority` e `recommendations` são os mesmos contratos de `radar.models`
 * serializados. Espelhar essa forma aqui — em vez de achatar — é o que mantém a
 * regra do projeto de não duplicar tipos entre camadas.
 */
export interface CompanyDetail {
  company_id: string;
  profile: Profile;
  classification?: { maturity: string; confidence: number; rationale?: string } | null;
  defensibility?: {
    axes: AxisOut[];
    total: number;
    commoditization_risk: number;
    global_confidence: number;
    weakest_axis: string;
    weights_version: string;
    computed_at?: string;
    actionable_gaps?: string[];
  } | null;
  priority?: {
    bucket: Bucket;
    urgency: number;
    capacity_to_act?: number;
    capacity_rationale?: string;
    recommended_next_step?: string;
  } | null;
  recommendations?: Recommendation[];
  has_briefing?: boolean;
  delta?: ScoreDelta | null;
}

export interface BriefingOut {
  briefing_id: string;
  company_id: string;
  markdown_url: string;
}

export interface SearchRunOut {
  id: string;
  query: string;
  status: string;
  max_companies?: number;
  created_at?: string;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    // A mensagem do backend é mais útil que "500": ele diz o que faltou.
    let detalhe = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detalhe = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* corpo não-JSON: o status já basta */
    }
    throw new Error(detalhe);
  }
  return (await res.json()) as T;
}

export const api = {
  fila: () => json<QueueItem[]>("/companies"),
  empresa: (id: string) => json<CompanyDetail>(`/companies/${id}`),
  buscar: (query: string, max_companies: number) =>
    json<SearchRunOut>("/searches", {
      method: "POST",
      body: JSON.stringify({ query, max_companies }),
    }),
  saude: () => json<{ status: string; [k: string]: unknown }>("/health"),
  briefing: (companyId: string) => json<BriefingOut>(`/companies/${companyId}/briefing`),
};
