"use client";

import { useState, useCallback, useEffect } from "react";
import { Plus, Pencil, Trash2 } from "lucide-react";
import {
  listAccounts,
  createAccount,
  updateAccount,
  deleteAccount,
} from "@/lib/portfolio-api";
import type {
  BrokerAccount,
  BrokerType,
  CreateAccountRequest,
} from "@/types/portfolio";
import { BROKER_LABELS } from "@/types/portfolio";

const BROKER_TYPES: BrokerType[] = [
  "fidelity",
  "heytrade",
  "ing",
  "interactive_brokers",
  "other",
];

const inputCls =
  "w-full rounded-[var(--radius)] border border-border bg-bg-input px-3 py-2 text-sm text-text placeholder:text-text-muted focus:border-accent-blue focus:outline-none";
const labelCls = "mb-1 block text-xs font-medium text-text-muted";

function Skeleton() {
  return (
    <div className="space-y-2">
      {[...Array(3)].map((_, i) => (
        <div key={i} className="skeleton h-16 rounded-[var(--radius)]" />
      ))}
    </div>
  );
}

// ─── Account Form ─────────────────────────────────────────────────────────────

interface AccountFormState {
  broker: BrokerType;
  name: string;
  currency: string;
  description: string;
}

function defaultFormState(): AccountFormState {
  return { broker: "other", name: "", currency: "EUR", description: "" };
}

function fromAccount(a: BrokerAccount): AccountFormState {
  return {
    broker: a.broker,
    name: a.name,
    currency: a.currency ?? "EUR",
    description: a.description ?? "",
  };
}

interface AccountFormProps {
  initial?: AccountFormState;
  saving: boolean;
  error: string | null;
  onSubmit: (data: AccountFormState) => void;
  onCancel: () => void;
  submitLabel: string;
}

function AccountForm({ initial, saving, error, onSubmit, onCancel, submitLabel }: AccountFormProps) {
  const [form, setForm] = useState<AccountFormState>(initial ?? defaultFormState());

  function set<K extends keyof AccountFormState>(k: K, v: AccountFormState[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSubmit(form);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label className={labelCls}>Broker *</label>
          <select
            value={form.broker}
            onChange={(e) => set("broker", e.target.value as BrokerType)}
            className={inputCls}
            required
          >
            {BROKER_TYPES.map((bt) => (
              <option key={bt} value={bt}>{BROKER_LABELS[bt]}</option>
            ))}
          </select>
        </div>
        <div>
          <label className={labelCls}>Account Name *</label>
          <input
            type="text"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="e.g. My ING account"
            className={inputCls}
            required
          />
        </div>
        <div>
          <label className={labelCls}>Base Currency</label>
          <input
            type="text"
            value={form.currency}
            onChange={(e) => set("currency", e.target.value.toUpperCase())}
            placeholder="EUR"
            maxLength={3}
            className={inputCls}
          />
        </div>
      </div>
      <div>
        <label className={labelCls}>Description</label>
        <textarea
          value={form.description}
          onChange={(e) => set("description", e.target.value)}
          placeholder="Optional"
          rows={2}
          className={`${inputCls} resize-none`}
        />
      </div>
      {error && (
        <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
          {error}
        </div>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving}
          className="rounded-[var(--radius)] bg-accent-blue/15 px-4 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25 disabled:opacity-50"
        >
          {saving ? "Saving…" : submitLabel}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded-[var(--radius)] border border-border px-4 py-1.5 text-sm text-text-muted hover:bg-bg-hover disabled:opacity-50"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

// ─── Account Row ──────────────────────────────────────────────────────────────

interface AccountRowProps {
  account: BrokerAccount;
  onEdit: (a: BrokerAccount) => void;
  onDelete: (a: BrokerAccount) => void;
}

function AccountRow({ account: a, onEdit, onDelete }: AccountRowProps) {
  return (
    <tr>
      <td className="px-4 py-3">
        <div className="font-medium text-text text-sm">{a.name}</div>
      </td>
      <td className="px-4 py-3 text-sm text-text-muted">{BROKER_LABELS[a.broker]}</td>
      <td className="px-4 py-3 text-sm text-text-muted font-mono">{a.currency ?? "—"}</td>
      <td className="px-4 py-3 text-xs text-text-muted truncate max-w-[200px]">{a.description ?? "—"}</td>
      <td className="px-3 py-3">
        <div className="flex items-center gap-2 justify-end">
          <button
            type="button"
            onClick={() => onEdit(a)}
            title="Edit account"
            className="rounded-[var(--radius)] p-1 text-text-muted hover:bg-bg-hover hover:text-text transition-colors"
            aria-label={`Edit ${a.name}`}
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            onClick={() => onDelete(a)}
            title="Delete account"
            className="rounded-[var(--radius)] p-1 text-text-muted hover:bg-bg-hover hover:text-accent-red transition-colors"
            aria-label={`Delete ${a.name}`}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </td>
    </tr>
  );
}

// ─── Delete Confirmation Modal ─────────────────────────────────────────────────

interface DeleteConfirmProps {
  account: BrokerAccount;
  onConfirm: () => void;
  onCancel: () => void;
  deleting: boolean;
  error: string | null;
}

function DeleteConfirmModal({ account, onConfirm, onCancel, deleting, error }: DeleteConfirmProps) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") onCancel(); }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/60 p-4"
      onClick={onCancel}
    >
      <div
        className="w-full max-w-[400px] rounded-[var(--radius)] border border-border bg-bg-card p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-text">Delete account?</h3>
        <p className="text-sm text-text-muted">
          Delete <strong className="text-text">{account.name}</strong>? This cannot be undone.
        </p>
        {error && (
          <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-3 py-2 text-sm text-accent-red">
            {error}
          </div>
        )}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onConfirm}
            disabled={deleting}
            className="rounded-[var(--radius)] bg-accent-red/15 px-4 py-1.5 text-sm text-accent-red hover:bg-accent-red/25 disabled:opacity-50"
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
          <button
            type="button"
            onClick={onCancel}
            disabled={deleting}
            className="rounded-[var(--radius)] border border-border px-4 py-1.5 text-sm text-text-muted hover:bg-bg-hover"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Main AccountsView ────────────────────────────────────────────────────────

type ViewMode = "list" | "create" | { edit: BrokerAccount };

export default function AccountsView() {
  const [accounts, setAccounts] = useState<BrokerAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [mode, setMode] = useState<ViewMode>("list");
  const [formSaving, setFormSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BrokerAccount | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setListError(null);
    try {
      const resp = await listAccounts();
      setAccounts(resp.accounts);
    } catch (err) {
      const e = err as { status?: number; data?: { detail?: string } };
      if (e.status === 503) {
        setListError("Portfolio storage is not yet configured.");
      } else {
        setListError(e.data?.detail ?? (err instanceof Error ? err.message : "Failed to load accounts"));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { load(); }, [load]);

  async function handleCreate(form: AccountFormState) {
    setFormSaving(true);
    setFormError(null);
    try {
      const req: CreateAccountRequest = {
        broker: form.broker,
        name: form.name,
        currency: form.currency || "EUR",
        description: form.description || undefined,
      };
      await createAccount(req);
      setMode("list");
      load();
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setFormError(e.data?.detail ?? (err instanceof Error ? err.message : "Failed to create account"));
    } finally {
      setFormSaving(false);
    }
  }

  async function handleUpdate(account: BrokerAccount, form: AccountFormState) {
    setFormSaving(true);
    setFormError(null);
    try {
      await updateAccount(account.account_id, {
        broker: form.broker,
        name: form.name,
        currency: form.currency || "EUR",
        description: form.description || undefined,
      });
      setMode("list");
      load();
    } catch (err) {
      const e = err as { data?: { detail?: string } };
      setFormError(e.data?.detail ?? (err instanceof Error ? err.message : "Failed to update account"));
    } finally {
      setFormSaving(false);
    }
  }

  async function handleDelete(account: BrokerAccount) {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteAccount(account.account_id);
      setDeleteTarget(null);
      load();
    } catch (err) {
      const e = err as { status?: number; data?: { detail?: string; error?: string } };
      if (e.status === 409) {
        setDeleteError(
          e.data?.detail ??
            "Cannot delete this account because it has associated movements. Reassign or delete the movements first.",
        );
      } else {
        setDeleteError(e.data?.detail ?? (err instanceof Error ? err.message : "Delete failed"));
      }
    } finally {
      setDeleting(false);
    }
  }

  if (loading) return <Skeleton />;

  if (listError) {
    return (
      <div className="rounded-[var(--radius)] border border-accent-red/40 bg-accent-red/10 px-4 py-3 text-sm">
        ⚠️ {listError}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Header actions */}
      {mode === "list" && (
        <div className="flex items-center justify-between">
          <span className="text-sm text-text-muted">{accounts.length} account{accounts.length !== 1 ? "s" : ""}</span>
          <button
            type="button"
            onClick={() => { setFormError(null); setMode("create"); }}
            className="inline-flex items-center gap-1.5 rounded-[var(--radius)] bg-accent-blue/15 px-3 py-1.5 text-sm text-accent-blue hover:bg-accent-blue/25"
          >
            <Plus size={15} />
            New Account
          </button>
        </div>
      )}

      {/* Create form */}
      {mode === "create" && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-5 py-4 space-y-3">
          <h3 className="text-sm font-semibold text-text">New Broker Account</h3>
          <AccountForm
            saving={formSaving}
            error={formError}
            onSubmit={handleCreate}
            onCancel={() => setMode("list")}
            submitLabel="Create Account"
          />
        </div>
      )}

      {/* Edit form */}
      {typeof mode === "object" && "edit" in mode && (
        <div className="rounded-[var(--radius)] border border-border bg-bg-card px-5 py-4 space-y-3">
          <h3 className="text-sm font-semibold text-text">Edit Account</h3>
          <AccountForm
            initial={fromAccount(mode.edit)}
            saving={formSaving}
            error={formError}
            onSubmit={(form) => handleUpdate(mode.edit, form)}
            onCancel={() => setMode("list")}
            submitLabel="Save Changes"
          />
        </div>
      )}

      {/* Account list */}
      {accounts.length === 0 && mode === "list" ? (
        <div className="rounded-[var(--radius-card)] border border-border bg-bg-card p-10 text-center space-y-3">
          <div className="text-3xl">🏦</div>
          <div className="text-base font-medium text-text">No broker accounts yet</div>
          <div className="text-sm text-text-muted">
            Add an account to assign your movements to specific brokers.
          </div>
          <button
            type="button"
            onClick={() => { setFormError(null); setMode("create"); }}
            className="inline-flex items-center gap-2 rounded-[var(--radius)] bg-[image:var(--grad-blue)] px-5 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            <Plus size={15} /> Add First Account
          </button>
        </div>
      ) : accounts.length > 0 ? (
        <div className="overflow-x-auto rounded-[var(--radius)] border border-border">
          <table className="w-full table-modern text-sm">
            <thead>
              <tr className="border-b border-border bg-bg-card/80">
                {["Account", "Broker", "Currency", "Notes", ""].map((h, i) => (
                  <th
                    key={i}
                    className={`px-4 py-2.5 text-xs font-semibold uppercase tracking-wide text-text-muted ${
                      i === 4 ? "text-right" : "text-left"
                    }`}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {accounts.map((a) => (
                <AccountRow
                  key={a.account_id}
                  account={a}
                  onEdit={(acc) => { setFormError(null); setMode({ edit: acc }); }}
                  onDelete={(acc) => { setDeleteError(null); setDeleteTarget(acc); }}
                />
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* Delete confirmation modal */}
      {deleteTarget && (
        <DeleteConfirmModal
          account={deleteTarget}
          onConfirm={() => handleDelete(deleteTarget)}
          onCancel={() => { setDeleteTarget(null); setDeleteError(null); }}
          deleting={deleting}
          error={deleteError}
        />
      )}
    </div>
  );
}
