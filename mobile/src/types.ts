export type Entity = { id: string; [key: string]: any };
export type Kind = "order" | "visit" | "task" | "interaction" | "closing";
export type State = "pending" | "acknowledged" | "conflict" | "rejected";
export interface Session {
  user: string;
  token: string;
  role: string;
  sectors: string[];
  mustChangePassword?: boolean;
}
export interface Snapshot {
  clients: Entity[];
  orders: Entity[];
  visits: Entity[];
  tasks: Entity[];
  routes: Entity[];
  goals: Entity[];
  prices: Entity[];
  priceTables: Entity[];
  industries: Entity[];
  interactions: Entity[];
  whatsappTemplates: Entity[];
  closings: Entity[];
  sellers: string[];
  role: string;
  sectors: string[];
  asOf?: string;
  [key: string]: any;
}
export interface Operation {
  changeId: string;
  type: Kind;
  data: Entity;
  base: Entity | null;
}
export interface Pending extends Operation {
  state: State;
  attempts: number;
  nextAt: number;
  error: string;
}
export interface Receipt {
  changeId: string;
  state: State;
  error?: string;
  remote?: Entity | null;
  canonical?: Entity;
}
export interface SyncResponse {
  results: Receipt[];
  snapshot: Snapshot;
}
export const collections: Record<Kind, string> = {
  order: "orders",
  visit: "visits",
  task: "tasks",
  interaction: "interactions",
  closing: "closings",
};
export const emptySnapshot = (): Snapshot => ({
  clients: [],
  orders: [],
  visits: [],
  tasks: [],
  routes: [],
  goals: [],
  prices: [],
  priceTables: [],
  industries: [],
  interactions: [],
  whatsappTemplates: [],
  closings: [],
  sellers: [],
  role: "",
  sectors: [],
});
