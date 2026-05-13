"use client";

export function CountEditor({
  value,
  max,
  onChange,
  disabled,
}: {
  value: number;
  max: number;
  onChange: (v: number) => void;
  disabled?: boolean;
}) {
  return (
    <input
      type="number"
      min={0}
      max={max}
      value={value}
      disabled={disabled}
      onChange={(e) => {
        const raw = Number(e.target.value);
        const n = Number.isFinite(raw) ? Math.max(0, Math.floor(raw)) : 0;
        onChange(Math.min(n, max));
      }}
      className="h-8 w-16 rounded-md border border-storesight-border bg-white px-2 text-right text-sm outline-none transition focus:border-storesight-accent disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark"
    />
  );
}
