"use client";

import { useEffect, useState } from "react";
import {
  createAdmin,
  createLead,
  createReviewer,
  deleteAdmin,
  deleteLead,
  deleteReviewer,
  listAdmins,
  listLeads,
  listReviewers,
  updateReviewer,
  type Admin,
  type Lead,
} from "@/lib/api";
import { REVIEWER_COLORS, reviewerColor, type Reviewer } from "@/lib/types";
import { useUser } from "@/lib/useUser";
import { useStore } from "@/lib/store";

type PersonFormProps = {
  disabled: boolean;
  onSubmit: (name: string, email: string) => Promise<void>;
  submitLabel: string;
};

function PersonForm({ disabled, onSubmit, submitLabel }: PersonFormProps) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handle = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim()) {
      setErr("Both name and email are required.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await onSubmit(name.trim(), email.trim());
      setName("");
      setEmail("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={handle} className="flex flex-col gap-2 sm:flex-row">
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Full name"
        disabled={disabled || busy}
        className="h-10 flex-1 rounded-lg border border-storesight-border bg-storesight-surface px-3 text-sm outline-none placeholder:text-storesight-ink-muted focus:border-storesight-accent focus:ring-2 focus:ring-storesight-accent-light/40 disabled:cursor-not-allowed disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark dark:placeholder:text-storesight-ink-muted-dark"
      />
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="name@storesight.com"
        disabled={disabled || busy}
        className="h-10 flex-1 rounded-lg border border-storesight-border bg-storesight-surface px-3 text-sm outline-none placeholder:text-storesight-ink-muted focus:border-storesight-accent focus:ring-2 focus:ring-storesight-accent-light/40 disabled:cursor-not-allowed disabled:opacity-50 dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-dark dark:placeholder:text-storesight-ink-muted-dark"
      />
      <button
        type="submit"
        disabled={disabled || busy}
        className="h-10 rounded-lg border border-storesight-accent bg-storesight-accent px-4 text-sm font-medium text-white transition hover:bg-storesight-primary disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? "Saving…" : submitLabel}
      </button>
      {err && (
        <p className="w-full text-xs text-storesight-hot-pink">{err}</p>
      )}
    </form>
  );
}

function ReviewerColorPicker({
  reviewer,
  disabled,
  onPick,
}: {
  reviewer: Reviewer;
  disabled: boolean;
  onPick: (color: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const current = reviewerColor(reviewer);

  const pick = async (color: string) => {
    setBusy(true);
    try {
      await onPick(color);
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        disabled={disabled || busy}
        onClick={() => setOpen((o) => !o)}
        title={disabled ? "Only admins can change colors" : "Change color"}
        className="h-6 w-6 rounded-full border border-storesight-border ring-2 ring-white transition hover:scale-110 disabled:cursor-not-allowed disabled:opacity-60 dark:border-storesight-border-dark dark:ring-storesight-surface-dark"
        style={{ background: current }}
        aria-label="Reviewer color"
      />
      {open && !disabled && (
        <>
          <button
            type="button"
            aria-hidden
            tabIndex={-1}
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-10 cursor-default"
          />
          <div className="absolute left-0 top-8 z-20 grid w-40 grid-cols-6 gap-1.5 rounded-xl border border-storesight-border bg-white p-2 shadow-xl dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
            {REVIEWER_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                disabled={busy}
                onClick={() => pick(c)}
                className={`h-5 w-5 rounded-full transition hover:scale-110 ${
                  current.toLowerCase() === c.toLowerCase()
                    ? "ring-2 ring-storesight-ink ring-offset-1 dark:ring-storesight-ink-dark dark:ring-offset-storesight-surface-dark"
                    : ""
                }`}
                style={{ background: c }}
                aria-label={`Set color ${c}`}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const { role, user } = useUser();
  const setReviewersStore = useStore((s) => s.setReviewers);
  const [reviewers, setReviewers] = useState<Reviewer[]>([]);
  const [admins, setAdmins] = useState<Admin[]>([]);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const isAdmin = role === "admin";

  const refresh = async () => {
    setLoading(true);
    setErr(null);
    try {
      const [rs, as, ls] = await Promise.all([listReviewers(), listAdmins(), listLeads()]);
      setReviewers(rs);
      setAdmins(as);
      setLeads(ls);
      setReviewersStore(rs);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const addReviewer = async (name: string, email: string) => {
    const created = await createReviewer(name, email);
    const next = [...reviewers, created];
    setReviewers(next);
    setReviewersStore(next);
  };
  const removeReviewer = async (id: string) => {
    await deleteReviewer(id);
    const next = reviewers.filter((r) => r.id !== id);
    setReviewers(next);
    setReviewersStore(next);
  };
  const changeReviewerColor = async (r: Reviewer, color: string) => {
    const updated = await updateReviewer(r.id, r.name, r.email, color);
    const next = reviewers.map((x) => (x.id === r.id ? { ...x, color: updated.color } : x));
    setReviewers(next);
    setReviewersStore(next);
  };

  const addAdmin = async (name: string, email: string) => {
    const created = await createAdmin(name, email);
    setAdmins([...admins, created]);
  };
  const removeAdmin = async (id: string) => {
    await deleteAdmin(id);
    setAdmins(admins.filter((a) => a.id !== id));
  };

  const addLead = async (name: string, email: string) => {
    const created = await createLead(name, email);
    setLeads([...leads, created]);
  };
  const removeLead = async (id: string) => {
    await deleteLead(id);
    setLeads(leads.filter((l) => l.id !== id));
  };

  return (
    <div className="mx-auto w-full max-w-4xl flex-1 px-6 py-10">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-storesight-ink dark:text-storesight-ink-dark">
          Settings
        </h1>
        <p className="mt-1 text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Manage the reviewers and admins who can sign in to this tool.
        </p>
        {!isAdmin && (
          <p className="mt-3 rounded-lg border border-storesight-border bg-storesight-bg-tint/40 px-3 py-2 text-xs text-storesight-ink-muted dark:border-storesight-border-dark dark:bg-storesight-surface-raised-dark dark:text-storesight-ink-muted-dark">
            You&apos;re signed in as <strong>{role}</strong>. Only admins can add or remove people.
          </p>
        )}
        {err && (
          <p className="mt-3 rounded-lg border border-storesight-hot-pink/40 bg-storesight-hot-pink/10 px-3 py-2 text-xs text-storesight-hot-pink">
            {err}
          </p>
        )}
      </div>

      {/* ---------------- Reviewers ---------------- */}
      <section className="mb-8 overflow-hidden rounded-2xl border border-storesight-border bg-storesight-surface shadow-sm dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <div className="h-1 w-full bg-storesight-accent/70 dark:bg-storesight-accent" />
        <div className="p-5">
        <h2 className="mb-1 text-base font-semibold text-storesight-ink dark:text-storesight-ink-dark">
          Reviewers
        </h2>
        <p className="mb-4 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Added by their name and email. When a reviewer signs in, they&apos;ll be recognized by email.
        </p>

        <PersonForm
          disabled={!isAdmin}
          onSubmit={addReviewer}
          submitLabel="Add reviewer"
        />

        <div className="mt-4 divide-y divide-storesight-border dark:divide-storesight-border-dark">
          {loading ? (
            <p className="py-6 text-center text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              Loading…
            </p>
          ) : reviewers.length === 0 ? (
            <p className="py-6 text-center text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              No reviewers yet. Add one above to get started.
            </p>
          ) : (
            reviewers.map((r) => (
              <div
                key={r.id}
                className="flex items-center justify-between gap-3 py-2.5"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <ReviewerColorPicker
                    reviewer={r}
                    disabled={!isAdmin}
                    onPick={(color) => changeReviewerColor(r, color)}
                  />
                  <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-storesight-ink dark:text-storesight-ink-dark">
                    {r.name}
                  </div>
                  <div className="truncate text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                    {r.email}
                    {user?.email?.toLowerCase() === r.email.toLowerCase() && (
                      <span className="ml-2 rounded-full bg-storesight-mint/30 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">
                        You
                      </span>
                    )}
                  </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => removeReviewer(r.id)}
                  disabled={!isAdmin}
                  className="rounded-lg border border-transparent px-2 py-1 text-xs text-storesight-ink-muted transition hover:border-storesight-hot-pink hover:text-storesight-hot-pink disabled:cursor-not-allowed disabled:opacity-40 dark:text-storesight-ink-muted-dark"
                >
                  Remove
                </button>
              </div>
            ))
          )}
        </div>
        </div>
      </section>

      {/* ---------------- Leads ---------------- */}
      <section className="mb-8 overflow-hidden rounded-2xl border border-storesight-border bg-storesight-surface shadow-sm dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <div className="h-1 w-full bg-storesight-sun/70 dark:bg-storesight-sun" />
        <div className="p-5">
          <h2 className="mb-1 text-base font-semibold text-storesight-ink dark:text-storesight-ink-dark">
            Leads
          </h2>
          <p className="mb-4 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
            Leads can publish and clear shift assignments and view team progress — but cannot manage the roster. They also appear on My Tasks like reviewers.
          </p>

          <PersonForm
            disabled={!isAdmin}
            onSubmit={addLead}
            submitLabel="Add lead"
          />

          <div className="mt-4 divide-y divide-storesight-border dark:divide-storesight-border-dark">
            {loading ? (
              <p className="py-6 text-center text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                Loading…
              </p>
            ) : leads.length === 0 ? (
              <p className="py-6 text-center text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                No leads yet.
              </p>
            ) : (
              leads.map((l) => (
                <div key={l.id} className="flex items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-storesight-ink dark:text-storesight-ink-dark">
                        {l.name}
                      </span>
                      {user?.email?.toLowerCase() === l.email.toLowerCase() && (
                        <span className="rounded-full bg-storesight-mint/30 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">
                          You
                        </span>
                      )}
                    </div>
                    <div className="truncate text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {l.email}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeLead(l.id)}
                    disabled={!isAdmin}
                    className="rounded-lg border border-transparent px-2 py-1 text-xs text-storesight-ink-muted transition hover:border-storesight-hot-pink hover:text-storesight-hot-pink disabled:cursor-not-allowed disabled:opacity-40 dark:text-storesight-ink-muted-dark"
                  >
                    Remove
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      {/* ---------------- Admins ---------------- */}
      <section className="overflow-hidden rounded-2xl border border-storesight-border bg-storesight-surface shadow-sm dark:border-storesight-border-dark dark:bg-storesight-surface-dark">
        <div className="h-1 w-full bg-storesight-primary/70 dark:bg-storesight-primary" />
        <div className="p-5">
        <h2 className="mb-1 text-base font-semibold text-storesight-ink dark:text-storesight-ink-dark">
          Admins
        </h2>
        <p className="mb-4 text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
          Admins can add or remove reviewers and admins, and change task assignments.
        </p>

        <PersonForm
          disabled={!isAdmin}
          onSubmit={addAdmin}
          submitLabel="Add admin"
        />

        <div className="mt-4 divide-y divide-storesight-border dark:divide-storesight-border-dark">
          {loading ? (
            <p className="py-6 text-center text-sm text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
              Loading…
            </p>
          ) : (
            admins.map((a) => {
              const isRoot = a.id === "__root__";
              return (
                <div
                  key={a.id}
                  className="flex items-center justify-between gap-3 py-2.5"
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-storesight-ink dark:text-storesight-ink-dark">
                        {a.name}
                      </span>
                      {isRoot && (
                        <span className="rounded-full bg-storesight-accent/20 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-storesight-primary dark:text-storesight-accent-light">
                          Root
                        </span>
                      )}
                      {user?.email?.toLowerCase() === a.email.toLowerCase() && (
                        <span className="rounded-full bg-storesight-mint/30 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300">
                          You
                        </span>
                      )}
                    </div>
                    <div className="truncate text-xs text-storesight-ink-muted dark:text-storesight-ink-muted-dark">
                      {a.email}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeAdmin(a.id)}
                    disabled={!isAdmin || isRoot}
                    title={isRoot ? "The root admin cannot be removed." : undefined}
                    className="rounded-lg border border-transparent px-2 py-1 text-xs text-storesight-ink-muted transition hover:border-storesight-hot-pink hover:text-storesight-hot-pink disabled:cursor-not-allowed disabled:opacity-40 dark:text-storesight-ink-muted-dark"
                  >
                    Remove
                  </button>
                </div>
              );
            })
          )}
        </div>
        </div>
      </section>
    </div>
  );
}
