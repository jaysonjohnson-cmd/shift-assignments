"use client";

import { useRouter } from "next/navigation";
import { AssignmentsOverview } from "@/components/assign/AssignmentsOverview";

/** Live check-in, reachable straight from the home tile.
 *
 * Its own route rather than a mode on /assignments: a same-route query param
 * left that page's state stuck on this view, so the assign menu became
 * unreachable after visiting it. AssignmentsOverview does its own role check.
 */
export default function AssignmentsOverviewPage() {
  const router = useRouter();
  return <AssignmentsOverview onBack={() => router.push("/assignments")} />;
}
