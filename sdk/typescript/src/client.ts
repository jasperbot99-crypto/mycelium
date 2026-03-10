import type {
  ApiErrorShape,
  ConnectRequest,
  ConnectResponse,
  CorroborateRequest,
  CorroborationResult,
  IngestRequest,
  IngestResponse,
  QueryFilters,
  QueryResult,
  ReplayEvent,
  ResolveConflictResult,
  Subscription,
  VerificationResult,
  VerifyRequest,
} from "./types.js";

export class MyceliumApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: "auth" | "validation" | "conflict" | "network" | "unknown",
    message: string,
  ) {
    super(message);
    this.name = "MyceliumApiError";
  }
}

export interface ClientOptions {
  baseUrl: string;
  apiKey: string;
  timeoutMs?: number;
  retries?: number;
}

export class MyceliumHttpClient {
  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly timeoutMs: number;
  private readonly retries: number;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.apiKey = options.apiKey;
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.retries = options.retries ?? 2;
  }

  async connect(request: ConnectRequest): Promise<ConnectResponse> {
    return this.request("POST", "/v1/agents/connect", request);
  }

  async disconnect(agentId: string): Promise<ConnectResponse> {
    return this.request("POST", `/v1/agents/${agentId}/disconnect`);
  }

  async ingest(agentId: string, request: IngestRequest): Promise<IngestResponse> {
    return this.request("POST", `/v1/agents/${agentId}/ingest`, request);
  }

  async query(
    agentId: string,
    question: string,
    filters?: QueryFilters,
    limit?: number,
  ): Promise<QueryResult[]> {
    return this.request("POST", `/v1/agents/${agentId}/query`, {
      question,
      filters,
      limit,
    });
  }

  async correct(
    agentId: string,
    factId: string,
    newContent: { subject: string; predicate: string; object: string; context?: string | null },
    reason: string,
  ): Promise<IngestResponse> {
    return this.request("POST", `/v1/agents/${agentId}/correct`, {
      fact_id: factId,
      new_content: newContent,
      reason,
    });
  }

  async verify(agentId: string, request: VerifyRequest): Promise<VerificationResult> {
    return this.request("POST", `/v1/agents/${agentId}/verify`, request);
  }

  async corroborate(agentId: string, request: CorroborateRequest): Promise<CorroborationResult> {
    return this.request("POST", `/v1/agents/${agentId}/corroborate`, request);
  }

  async resolveConflicts(agentId: string, limit = 100): Promise<ResolveConflictResult[]> {
    return this.request("POST", `/v1/agents/${agentId}/resolve-conflicts`, { limit });
  }

  async traceProvenance(agentId: string, factId: string, maxDepth = 16): Promise<unknown[]> {
    return this.request(
      "GET",
      `/v1/agents/${agentId}/provenance/${factId}?max_depth=${encodeURIComponent(String(maxDepth))}`,
    );
  }

  async updateContext(
    agentId: string,
    context: { task?: string | null; entities?: string[]; urgency?: "normal" | "elevated" | "critical" },
  ): Promise<void> {
    await this.request("POST", `/v1/agents/${agentId}/context`, { context });
  }

  async updateSubscriptions(agentId: string, subscriptions: Subscription[]): Promise<Subscription[]> {
    return this.request("PUT", `/v1/agents/${agentId}/subscriptions`, { subscriptions });
  }

  async getSubscriptions(agentId: string): Promise<Subscription[]> {
    return this.request("GET", `/v1/agents/${agentId}/subscriptions`);
  }

  async replay(agentId: string, deliver = false): Promise<ReplayEvent[]> {
    return this.request("POST", `/v1/agents/${agentId}/replay`, { deliver });
  }

  async ack(agentId: string, eventId: string): Promise<void> {
    await this.request("POST", `/v1/agents/${agentId}/ack`, { event_id: eventId });
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const doRequest = async (): Promise<T> => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const response = await fetch(`${this.baseUrl}${path}`, {
          method,
          headers: {
            Authorization: `Bearer ${this.apiKey}`,
            "Content-Type": "application/json",
          },
          body: body === undefined ? undefined : JSON.stringify(body),
          signal: controller.signal,
        });

        if (!response.ok) {
          const payload = (await this.safeJson(response)) as ApiErrorShape;
          throw this.toApiError(response.status, payload);
        }

        if (response.status === 204) {
          return undefined as T;
        }

        return (await response.json()) as T;
      } catch (error) {
        if (error instanceof MyceliumApiError) {
          throw error;
        }
        throw new MyceliumApiError(0, "network", this.getErrorMessage(error));
      } finally {
        clearTimeout(timeout);
      }
    };

    let attempt = 0;
    while (true) {
      try {
        return await doRequest();
      } catch (error) {
        const retriable =
          error instanceof MyceliumApiError &&
          (error.code === "network" || error.status >= 500);
        if (!retriable || attempt >= this.retries) {
          throw error;
        }
        attempt += 1;
      }
    }
  }

  private toApiError(status: number, payload: ApiErrorShape): MyceliumApiError {
    const message = payload.detail ?? payload.error ?? `HTTP ${status}`;
    if (status === 401 || status === 403) {
      return new MyceliumApiError(status, "auth", message);
    }
    if (status === 409) {
      return new MyceliumApiError(status, "conflict", message);
    }
    if (status >= 400 && status < 500) {
      return new MyceliumApiError(status, "validation", message);
    }
    return new MyceliumApiError(status, "unknown", message);
  }

  private async safeJson(response: Response): Promise<unknown> {
    try {
      return await response.json();
    } catch {
      return {};
    }
  }

  private getErrorMessage(error: unknown): string {
    if (error instanceof Error) {
      return error.message;
    }
    return "Unknown network error";
  }
}
