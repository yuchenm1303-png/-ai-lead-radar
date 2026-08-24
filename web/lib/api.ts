export type LeadStatus = "new" | "saved" | "contacted" | "ignored";

export type ApiLead = {
  id: number;
  source: string;
  title: string;
  excerpt: string;
  category: string;
  score: number;
  published_at: string;
  budget: string | null;
  status: LeadStatus;
  url: string | null;
  signals: string[];
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export async function fetchLeads(minScore = 0): Promise<ApiLead[]> {
  const response = await fetch(`${API_BASE}/api/v1/leads?min_score=${minScore}`, {
    cache: "no-store",
  });
  if (!response.ok) throw new Error(`Failed to load leads: ${response.status}`);
  return response.json();
}

export async function scanNow(): Promise<{ scanned: number; high_intent: number }> {
  const response = await fetch(`${API_BASE}/api/v1/monitor/scan`, { method: "POST" });
  if (!response.ok) throw new Error(`Scan failed: ${response.status}`);
  return response.json();
}

export async function updateLeadStatus(id: number, status: LeadStatus): Promise<ApiLead> {
  const response = await fetch(`${API_BASE}/api/v1/leads/${id}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!response.ok) throw new Error(`Status update failed: ${response.status}`);
  return response.json();
}
