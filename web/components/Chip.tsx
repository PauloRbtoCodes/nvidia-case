import { BUCKET_LABEL, BUCKET_VARS, type Bucket } from "@/lib/api";

/** Bucket como pastilha: cor + marcador + rótulo textual, nunca só a cor. */
export function BucketChip({ bucket, big = false }: { bucket: Bucket; big?: boolean }) {
  const v = BUCKET_VARS[bucket] ?? BUCKET_VARS.monitorar;
  return (
    <span
      className="chip"
      style={{
        ["--chip-fg" as string]: v.fg,
        ["--chip-wash" as string]: v.wash,
        ...(big ? { fontSize: 14, padding: "6px 14px" } : {}),
      }}
    >
      {BUCKET_LABEL[bucket] ?? bucket}
    </span>
  );
}
