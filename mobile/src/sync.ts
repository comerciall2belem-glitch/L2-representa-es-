import { ApiError, L2API } from "./api";
import { LocalStore } from "./store";
export class SyncEngine {
  running = false;
  constructor(
    public store: LocalStore,
    public api: L2API,
    public id: () => string,
    public changed: () => void = () => {},
  ) {}
  async run(now = Date.now()) {
    if (this.running) return;
    this.running = true;
    const owner = this.id();
    let leased = false;
    try {
      leased = await this.store.acquire(owner, now);
      if (!leased) return;
      // Refresh authorization/portfolio before transmitting queued records.
      await this.store.apply(await this.api.snapshot());
      this.changed();
      for (const item of await this.store.pending()) {
        if (item.state !== "pending" || item.nextAt > now) continue;
        await this.store.renew(owner, Date.now());
        try {
          const response = await this.api.sync({
            changeId: item.changeId,
            type: item.type,
            data: item.data,
            base: item.base,
          });
          if (
            response.results.length !== 1 ||
            response.results[0].changeId !== item.changeId
          )
            throw new Error("Recibo ausente; operação preservada.");
          await this.store.apply(response.snapshot, response.results);
          this.changed();
        } catch (error) {
          const msg =
            error instanceof Error ? error.message : "Falha de comunicação";
          if (error instanceof ApiError && [401, 403].includes(error.status)) {
            await this.store.retainError(item.changeId, msg);
            throw error;
          }
          if (
            error instanceof ApiError &&
            error.status >= 400 &&
            error.status < 500 &&
            ![408, 429].includes(error.status)
          ) {
            // Preserve data; classify rejection only if backend explicitly returned definitive HTTP failure.
            await this.store.apply(await this.store.snapshot(), [
              { changeId: item.changeId, state: "rejected", error: msg },
            ]);
            this.changed();
            continue;
          }
          await this.store.retry(item.changeId, now, msg);
          throw error;
        }
      }
    } finally {
      if (leased) await this.store.release(owner);
      this.running = false;
    }
  }
}
