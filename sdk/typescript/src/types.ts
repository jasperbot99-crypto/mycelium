export type SourceType =
  | "human_correction"
  | "system_verification"
  | "agent_extraction"
  | "agent_inference";

export type Priority = "low" | "normal" | "high" | "critical";

export type VerificationMethod =
  | "system_probe"
  | "cross_agent_corroboration"
  | "temporal_check"
  | "source_recheck"
  | "manual_review";

export type VerificationStatus = "unverified" | "verified" | "failed" | "stale";

export interface FactContent {
  subject: string;
  predicate: string;
  object: string;
  context?: string | null;
}

export interface Fact {
  id: string;
  content: FactContent;
  source_agent_id: string;
  source_type: SourceType;
  confidence: number;
  trust_score: number;
  tags: string[];
  conflict_status: string;
  metadata: Record<string, unknown>;
  created_at: string;
  valid_from: string;
}

export interface QueryFilters {
  min_confidence?: number;
  source_types?: SourceType[];
  tags?: string[];
  subject?: string;
  active_only?: boolean;
  include_conflicted?: boolean;
}

export interface QueryResult {
  fact: Fact;
  score: number;
  similarity: number;
}

export interface IngestRequest {
  content: FactContent;
  source_type: SourceType;
  tags?: string[];
  derived_from?: string[];
  metadata?: Record<string, unknown>;
  initial_confidence?: number;
}

export interface IngestResponse {
  accepted: boolean;
  fact?: Fact;
  rejection?: {
    code: string;
    message: string;
    existing_fact_id?: string;
  };
  contradiction_fact_ids: string[];
  corroboration_fact_ids: string[];
}

export interface ConnectRequest {
  agent_id: string;
  role?: string;
  subscriptions?: Subscription[];
}

export interface ConnectResponse {
  agent_id: string;
  connected: boolean;
  connected_since?: string | null;
}

export interface Subscription {
  topic: string;
  priority?: Priority;
  min_confidence?: number;
  source_types?: SourceType[];
}

export interface VerifyRequest {
  fact_id: string;
  method: VerificationMethod;
  status: VerificationStatus;
  reason?: string;
}

export interface VerificationResult {
  fact_id: string;
  status: VerificationStatus;
  method: VerificationMethod;
  previous_status: VerificationStatus;
  verified_at: string;
  confidence_delta: number;
  trust_delta: number;
  reason?: string | null;
}

export interface CorroborateRequest {
  fact_id: string;
  corroborating_fact_id: string;
  reason?: string;
}

export interface CorroborationResult {
  fact_id: string;
  corroborating_fact_id: string;
  corroboration_count: number;
  confidence_delta: number;
  trust_delta: number;
  relation_created: boolean;
  verified_at: string;
}

export interface ResolveConflictResult {
  conflict_id: string;
  status: string;
  winning_fact_id?: string | null;
  resolved_by: string;
  resolution: Record<string, unknown>;
}

export interface ReplayEvent {
  id: string;
  fact_id: string;
  target_agent_id: string;
  reason: string;
  priority: Priority;
  delivered: boolean;
  delivered_at?: string | null;
  created_at: string;
  sequence_num: number;
}

export interface ApiErrorShape {
  detail?: string;
  error?: string;
}
