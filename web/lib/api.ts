export type LeadStatus = "new" | "saved" | "contacted" | "ignored";
export type UrgencyLevel = "low" | "normal" | "high" | "urgent";

export type ApiLead = {
  id: number;
  source: string;
  external_id: string | null;
  source_id: string | null;
  title: string;
  excerpt: string;
  content: string;
  category: string;
  need_type: string;
  score: number;
  ai_score: number;
  published_at: string;
  discovered_at: string | null;
  budget: string | null;
  budget_text: string | null;
  status: LeadStatus;
  url: string | null;
  signals: string[];
  is_lead: boolean;
  intent_score: number;
  fit_score: number;
  freshness_score: number;
  urgency: UrgencyLevel;
  reason: string;
  confidence: number;
  dedupe_key: string;
  notified_at: string | null;
  created_at: string;
  updated_at: string;
};

export type MonitorStatus = {
  running: boolean;
  mode: string;
  platforms: string[];
  connectors: string[];
  ai_provider: string;
  notification_enabled: boolean;
  last_scan_at: string | null;
  last_scan_counts: Record<string, number>;
  note: string | null;
};

export type IngestItem = {
  source?: string;
  source_id?: string | null;
  title: string;
  content?: string;
  url?: string | null;
  published_at?: string;
  budget_text?: string | null;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

function headers(writeToken?: string): HeadersInit {
  const result: Record<string, string> = { "Content-Type": "application/json" };
  if (writeToken) result["X-Radar-Token"] = writeToken;
  return result;
}

export async function fetchLeads(minScore = 0): Promise<ApiLead[]> {
  const response = await fetch(`${API_BASE}/api/v1/leads?min_score=${minScore}&is_lead=true`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Failed to load leads: ${response.status}`);
  return response.json();
}

export async function fetchMonitorStatus(): Promise<MonitorStatus> {
  const response = await fetch(`${API_BASE}/api/v1/monitor/status`, { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to load monitor status: ${response.status}`);
  return response.json();
}

export async function scanNow(writeToken?: string): Promise<{
  scanned: number;
  stored: number;
  created: number;
  high_intent: number;
  notified: number;
}> {
  const response = await fetch(`${API_BASE}/api/v1/monitor/scan`, {
    method: "POST",
    headers: headers(writeToken),
  });
  if (!response.ok) throw new Error(`Scan failed: ${response.status}`);
  return response.json();
}

export async function updateLeadStatus(id: number, status: LeadStatus, writeToken?: string): Promise<ApiLead> {
  const response = await fetch(`${API_BASE}/api/v1/leads/${id}/status`, {
    method: "PATCH",
    headers: headers(writeToken),
    body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(`Status update failed: ${response.status}`);
  return response.json();
}

export async function ingestItems(
  items: IngestItem[],
  adapter: "manual" | "browser-helper" = "manual",
  writeToken?: string,
): Promise<{ received: number; stored: number; created: number; filtered_out: number; leads: ApiLead[] }> {
  const response = await fetch(`${API_BASE}/api/v1/ingest`, {
    method: "POST",
    headers: headers(writeToken),
    body: JSON.stringify({ adapter, items }),
  });
  if (!response.ok) throw new Error(`Ingest failed: ${response.status}`);
  return response.json();
}
