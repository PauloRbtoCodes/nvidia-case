import { BUCKET_LABEL, BUCKET_VARS, type Bucket } from "@/lib/api";

/**
 * O bucket é a decisão de agenda, não um enfeite: "abordar agora" e "nutrir"
 * são conversas diferentes. Um quadrado da cor mais a palavra — a cor reforça,
 * a palavra informa, e quem não distingue matiz lê o mesmo conteúdo.
 */
export function BucketChip({ bucket, big = false }: { bucket: Bucket; big?: boolean }) {
  const v = BUCKET_VARS[bucket] ?? BUCKET_VARS.monitorar;
  return (
    <span
      className={`bucket${big ? " is-big" : ""}`}
      style={{ ["--bucket-fg" as string]: v.fg }}
    >
      {BUCKET_LABEL[bucket] ?? bucket}
    </span>
  );
}
