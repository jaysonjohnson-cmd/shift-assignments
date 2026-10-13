/**
 * Collection Review pulls responses in "loads": each Search / Load more checks
 * out one Load size worth of groups to whoever clicked. That's how a split
 * JID is divided — each sharer pulls in their own share and no more — so My
 * Tasks spells out which loads add up to it.
 */

/** Load sizes Collection Review offers, largest first. */
export const LOAD_SIZES = [200, 100, 50, 25] as const;

/**
 * Loads that add up to `share`, rounded to the nearest 25 (at least one 25):
 * 75 → [50, 25], 150 → [100, 50]. Empty for a share of 0.
 */
export function planLoads(share: number): number[] {
  if (!(share > 0)) return [];
  let left = Math.max(25, Math.round(share / 25) * 25);
  const loads: number[] = [];
  for (const size of LOAD_SIZES) {
    while (left >= size) {
      loads.push(size);
      left -= size;
    }
  }
  return loads;
}

/** "Load size 50, Search · then 25, Load more" for a plan from planLoads. */
export function describeLoads(loads: number[]): string {
  if (loads.length === 0) return "";
  if (loads.length === 1) return `Load size ${loads[0]}, Search once — don't Load more`;
  const steps = loads.map((size, i) => {
    const same = i > 0 && size === loads[i - 1];
    if (i === 0) return `Load size ${size}, Search`;
    return same ? "Load more" : `${size}, Load more`;
  });
  return `${steps[0]} · then ${steps.slice(1).join(" · then ")}`;
}
