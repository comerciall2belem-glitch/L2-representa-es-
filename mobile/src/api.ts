import type { Operation, Session, Snapshot, SyncResponse } from "./types";
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export class L2API {
  constructor(
    public base: string,
    public token = "",
    public fetcher: typeof fetch = fetch,
  ) {
    const url = new URL(base);
    if (
      url.protocol !== "https:" &&
      !(
        url.protocol === "http:" &&
        ["127.0.0.1", "localhost", "10.0.2.2"].includes(url.hostname)
      )
    )
      throw new Error("Servidor deve usar HTTPS.");
    this.base = base.replace(/\/$/, "");
  }
  async request(path: string, body?: unknown, method?: string): Promise<any> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await this.fetcher(this.base + path, {
        method: method || (body === undefined ? "GET" : "POST"),
        headers: {
          "Content-Type": "application/json",
          ...(this.token ? { Authorization: "Bearer " + this.token } : {}),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok)
        throw new ApiError(
          response.status,
          typeof data.detail === "string"
            ? data.detail
            : data.message || `Falha ${response.status}`,
        );
      return data;
    } finally {
      clearTimeout(timeout);
    }
  }
  async login(user: string, password: string): Promise<Session> {
    const s = await this.request("/api/login", { user, password });
    this.token = s.token;
    return s;
  }
  async changePassword(currentPassword: string, newPassword: string) {
    return this.request("/api/change-password", {
      currentPassword,
      newPassword,
    });
  }
  async snapshot(): Promise<Snapshot> {
    return this.request("/api/mobile/v1/snapshot");
  }
  async sync(op: Operation): Promise<SyncResponse> {
    return this.request("/api/mobile/v1/sync", { changes: [op] });
  }
  async logout() {
    return this.request("/api/logout", {});
  }
}
