export function formatRelative(
  input: string | Date | null | undefined,
): string {
  if (!input) return "";
  const date = input instanceof Date ? input : new Date(input);
  const ms = date.getTime();
  if (Number.isNaN(ms)) return "";

  const diffSec = Math.round((Date.now() - ms) / 1000);
  const abs = Math.abs(diffSec);
  const suffix = diffSec >= 0 ? "ago" : "from now";

  if (abs < 60) return "just now";
  if (abs < 3600) return `${Math.round(abs / 60)}m ${suffix}`;
  if (abs < 86400) return `${Math.round(abs / 3600)}h ${suffix}`;
  if (abs < 604800) return `${Math.round(abs / 86400)}d ${suffix}`;
  return date.toLocaleDateString();
}

export function formatPending(
  input: string | Date | null | undefined,
): { label: string; hours: number } | null {
  if (!input) return null;
  const date = input instanceof Date ? input : new Date(input);
  const ms = date.getTime();
  if (Number.isNaN(ms)) return null;
  const diffMs = Date.now() - ms;
  const hours = diffMs / 3_600_000;
  if (hours < 1) return { label: `Pending ${Math.max(1, Math.round(diffMs / 60000))}m`, hours };
  if (hours < 24) return { label: `Pending ${Math.round(hours)}h`, hours };
  return { label: `Pending ${Math.round(hours / 24)}d`, hours };
}
