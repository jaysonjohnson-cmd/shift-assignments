"use client";

import type {
  MyTasksResponse,
  ProjectSummary,
  Reviewer,
  Row,
  ShiftSnapshot,
} from "./types";

export type Role = "admin" | "lead" | "reviewer" | "viewer";

export type Me = {
  email: string;
  name: string;
  role: Role;
};

export type Admin = {
  id: string;
  name: string;
  email: string;
};

async function call<T>(
  method: "GET" | "POST" | "PUT" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const resp = await fetch(path, {
    method,
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    let message = `${method} ${path} failed with ${resp.status}`;
    try {
      const data = await resp.json();
      if (data?.error) message = data.error;
    } catch {
      // non-JSON error body — keep the default message
    }
    throw new Error(message);
  }
  return (await resp.json()) as T;
}

export async function getMe(): Promise<Me> {
  return call<Me>("GET", "/api/me");
}

export async function listReviewers(): Promise<Reviewer[]> {
  const resp = await call<{ data: Reviewer[] }>("GET", "/api/reviewers");
  return resp.data;
}

export async function createReviewer(
  name: string,
  email: string,
  color?: string,
): Promise<Reviewer> {
  const resp = await call<{ data: Reviewer }>("POST", "/api/reviewers", {
    name,
    email,
    color,
  });
  return resp.data;
}

export async function updateReviewer(
  id: string,
  name: string,
  email: string,
  color?: string,
): Promise<Reviewer> {
  const resp = await call<{ data: Reviewer }>("PUT", `/api/reviewers/${id}`, {
    name,
    email,
    color,
  });
  return resp.data;
}

export async function deleteReviewer(id: string): Promise<void> {
  await call<{ data: { id: string } }>("DELETE", `/api/reviewers/${id}`);
}

export async function listAdmins(): Promise<Admin[]> {
  const resp = await call<{ data: Admin[] }>("GET", "/api/admins");
  return resp.data;
}

export async function createAdmin(name: string, email: string): Promise<Admin> {
  const resp = await call<{ data: Admin }>("POST", "/api/admins", {
    name,
    email,
  });
  return resp.data;
}

export async function deleteAdmin(id: string): Promise<void> {
  await call<{ data: { id: string } }>("DELETE", `/api/admins/${id}`);
}

export type Lead = {
  id: string;
  name: string;
  email: string;
};

export async function listLeads(): Promise<Lead[]> {
  const resp = await call<{ data: Lead[] }>("GET", "/api/leads");
  return resp.data;
}

export async function createLead(name: string, email: string): Promise<Lead> {
  const resp = await call<{ data: Lead }>("POST", "/api/leads", { name, email });
  return resp.data;
}

export async function deleteLead(id: string): Promise<void> {
  await call<{ data: { id: string } }>("DELETE", `/api/leads/${id}`);
}

// ---------- Bloom feed + shift snapshots + My Tasks ----------

export async function getSubmissionAges(): Promise<{ data: Record<string, string>; loading: boolean }> {
  return call<{ data: Record<string, string>; loading: boolean }>(
    "GET",
    "/api/bloom/submission-ages",
  );
}

export async function getBloomJobs(force = false, status?: string): Promise<Row[]> {
  const params = new URLSearchParams();
  if (force) params.set("force", "1");
  if (status) params.set("status", status);
  const qs = params.toString() ? `?${params.toString()}` : "";
  const resp = await call<{ data: Row[] }>("GET", `/api/bloom/jobs${qs}`);
  return resp.data;
}

export async function getBloomProjects(): Promise<ProjectSummary[]> {
  const resp = await call<{ data: ProjectSummary[] }>(
    "GET",
    "/api/bloom/projects",
  );
  return resp.data;
}

export async function publishShift(
  assignments: Record<string, Row[]>,
): Promise<{ id: string; published_at: string }> {
  const resp = await call<{ data: { id: string; published_at: string } }>(
    "POST",
    "/api/shifts/publish",
    { assignments },
  );
  return resp.data;
}

export async function getLatestShift(): Promise<ShiftSnapshot | null> {
  const resp = await call<{ data: ShiftSnapshot | null }>(
    "GET",
    "/api/shifts/latest",
  );
  return resp.data;
}

export async function getMyTasks(): Promise<MyTasksResponse> {
  const resp = await call<{ data: MyTasksResponse }>("GET", "/api/shifts/my");
  return resp.data;
}

export async function markTaskDone(jobId: string, note?: string): Promise<void> {
  await call<{ data: unknown }>("POST", "/api/shifts/my/complete", {
    job_id: jobId,
    note,
  });
}

export async function unmarkTaskDone(jobId: string): Promise<void> {
  await call<{ data: unknown }>(
    "DELETE",
    `/api/shifts/my/complete/${encodeURIComponent(jobId)}`,
  );
}

export type Completion = {
  id: string;
  kind: "completion";
  reviewer_email: string;
  project_id: string;
  shift_snapshot_id: string;
  completed_at: string;
  note?: string;
};

export async function listAllCompletions(): Promise<{
  snapshot_id: string | null;
  completions: Completion[];
}> {
  const resp = await call<{
    data: { snapshot_id: string | null; completions: Completion[] };
  }>("GET", "/api/shifts/completions");
  return resp.data;
}

export async function resetAllCompletions(): Promise<{ deleted: number }> {
  const resp = await call<{ data: { deleted: number } }>(
    "DELETE",
    "/api/shifts/completions",
  );
  return resp.data;
}

export type ReviewerProgress = {
  email: string;
  name: string;
  total: number;
  completed: number;
  pending: number;
  first_priority: number | null;
  last_priority: number | null;
};

export type ShiftOverview = {
  snapshot_id: string | null;
  published_at?: string;
  reviewers: ReviewerProgress[];
};

export async function getShiftOverview(): Promise<ShiftOverview> {
  const resp = await call<{ data: ShiftOverview }>("GET", "/api/shifts/overview");
  return resp.data;
}

export type ShiftJob = {
  id: string;
  projectId: string;
  jobId: string;
  priority: number | null;
  unreviewedCount: number;
  name: string;
  completed: boolean;
  oldestSubmission: string;
  groupIds: string[];
};

export type ReviewerJobs = {
  email: string;
  name: string;
  color?: string;
  jobs: ShiftJob[];
};

export type ShiftJobs = {
  snapshot_id: string | null;
  published_at?: string;
  jobs_by_reviewer: ReviewerJobs[];
};

export async function getShiftJobs(): Promise<ShiftJobs> {
  const resp = await call<{ data: ShiftJobs }>("GET", "/api/shifts/jobs");
  return resp.data;
}


export type ClearMode = "active" | "completed" | "all" | "reset";

/**
 * Clear shift tasks/completions. Pass `reviewerEmail` to scope the clear to a
 * single reviewer — the shift stays live for everyone else.
 */
export async function clearShift(
  mode: ClearMode,
  reviewerEmail?: string,
): Promise<{
  mode: ClearMode;
  cleared_rows: number;
  cleared_completions: number;
}> {
  const body: { mode: ClearMode; reviewer_email?: string } = { mode };
  if (reviewerEmail) body.reviewer_email = reviewerEmail;
  const resp = await call<{
    data: { mode: ClearMode; cleared_rows: number; cleared_completions: number };
  }>("POST", "/api/shifts/clear", body);
  return resp.data;
}
