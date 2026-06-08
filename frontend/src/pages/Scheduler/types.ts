/**
 * Shared type definitions for the Scheduler page (v2 spawn-tree aware).
 *
 * Kept in a dedicated file so `index.tsx`, `GraphView.tsx` and
 * `TimelineView.tsx` can all consume the same shape without circular imports.
 */

export interface TaskPermissions {
  read_dirs?: string[];
  write_dirs?: string[];
}

export interface TaskConfig {
  script_path?: string;
  script_args?: string[];
  prompt?: string;
  doc_path?: string | string[];
  capabilities?: string[];
  permissions?: TaskPermissions;
}

export interface ReplyTo {
  channel?: string;
  session_id?: string;
}

export interface StepData {
  type: string;
  ts?: string;
  content?: string;
  tool?: string;
  args_preview?: string;
  result_preview?: string;
  actions?: unknown[];
  prompt?: string;
  args?: string[];
  doc_paths?: string[];
  capabilities?: string[];
  read_dirs?: string[];
  write_dirs?: string[];
  resolved_write_dirs?: unknown;
  scripts_dir?: string;
  fs_dir?: string;
}

export interface RunData {
  run_id?: string;
  status: string;
  started_at?: string;
  finished_at?: string;
  steps?: StepData[];
  output?: string;
}

/**
 * Task metadata as returned by GET /scheduler/{task_id}.
 *
 * The v2 spawn-tree fields below are populated by the backend even on legacy
 * tasks (defaults: parent=null, root=self, chain=[], depth=0, children=0).
 */
export interface TaskData {
  id: string;
  name: string;
  description?: string;
  task_type: string;
  schedule_type: string;
  schedule?: string;
  enabled?: boolean;
  created_at?: string;
  last_run_at?: string;
  next_run_at?: string;
  run_count?: number;
  reply_to?: ReplyTo;
  task_config?: TaskConfig;
  runs?: RunData[];
  service_id?: string;

  // ── v2 spawn-tree fields ────────────────────────────────────────────────
  parent_task_id?: string | null;
  root_task_id?: string;
  spawn_chain?: string[];      // [root, ..., parent]; empty for root
  spawn_depth?: number;        // 0 = root
  spawn_reason?: string;
  children_count?: number;
  descendants_count?: number;
  descendants_summary?: string;

  // ── client-side annotation (not on backend) ─────────────────────────────
  _scope?: 'admin' | 'service';
}

/** Recursive walk_tree response shape (matches scheduler_tree.walk_tree). */
export interface TaskTreeNode {
  meta: TaskData;
  children: TaskTreeNode[];
  truncated?: boolean;
}

/** Spawn rate-limit quota peek response shape (matches QuotaResult.as_dict). */
export interface ChainQuota {
  allowed: boolean;
  current: number;
  limit: number;
  remaining: number;
  window_seconds: number;
  reset_at: string;
}
