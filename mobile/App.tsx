import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  Alert,
  Image,
  AppState,
  FlatList,
  Linking,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useColorScheme,
  useWindowDimensions,
  KeyboardAvoidingView,
  Platform,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import NetInfo from "@react-native-community/netinfo";
import EventSource from "react-native-sse";
import { Feather } from "@expo/vector-icons";
import QRView from "./src/QRView";
import { cleanText, extractText, catalogLink } from "./src/utilities";
import * as Clipboard from "expo-clipboard";
import * as Location from "expo-location";
import { L2API, ApiError } from "./src/api";
import { LocalStore } from "./src/store";
import { SyncEngine } from "./src/sync";
import { downloadCatalog, shareCatalog } from "./src/catalog";
import {
  currency,
  cents,
  dailyMetrics,
  makeOrder,
  orderTotal,
  phone,
  template,
  workDate,
} from "./src/domain";
import {
  emptySnapshot,
  type Entity,
  type Pending,
  type Snapshot,
} from "./src/types";
import {
  forget,
  id,
  openStore,
  registerBackground,
  remember,
  restored,
  type SavedSession,
} from "./src/native";

const DEFAULT_BASE =
  process.env.EXPO_PUBLIC_L2_API_URL || "https://l2-one.onrender.com";
const TABS = ["Hoje", "Clientes", "Pedidos", "Maleta", "Pendências"] as const;
type Tab = (typeof TABS)[number];
const CHECKS = [
  "Conferir mix e estoque",
  "Identificar rupturas",
  "Apresentar lançamentos",
  "Revisar exposição e provadores",
  "Registrar oportunidade",
  "Combinar próxima ação",
];
const initialSnippets = [
  {
    id: "welcome",
    title: "Apresentação",
    body: "Olá, {{nome_cliente}}! Aqui é {{vendedor}}, da L2 Representações. Vamos conversar sobre as oportunidades para sua loja?",
  },
  {
    id: "restock",
    title: "Reposição",
    body: "Olá, {{nome_cliente}}! Como está o estoque da loja? Posso ajudar a organizar a reposição e apresentar nossos lançamentos.",
  },
  {
    id: "followup",
    title: "Retorno comercial",
    body: "Olá, {{nome_cliente}}! Estou retomando nosso atendimento. Podemos avançar com a proposta ou ajustar o mix para sua loja?",
  },
];

type Theme = {
  bg: string;
  card: string;
  text: string;
  muted: string;
  border: string;
  accent: string;
};
const ThemeContext = createContext<Theme>({
  bg: "#f1f5f5",
  card: "#fff",
  text: "#153b43",
  muted: "#526d73",
  border: "#dce7e7",
  accent: "#b48c45",
});
function Button({
  title,
  onPress,
  secondary = false,
  disabled = false,
}: {
  title: string;
  onPress: () => void;
  secondary?: boolean;
  disabled?: boolean;
}) {
  const theme = useContext(ThemeContext);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      disabled={disabled}
      onPress={onPress}
      style={[
        styles.button,
        {
          backgroundColor: secondary ? theme.card : theme.text,
          borderColor: theme.border,
          opacity: disabled ? 0.5 : 1,
        },
      ]}
    >
      <Text
        style={{ color: secondary ? theme.text : theme.bg, fontWeight: "700" }}
      >
        {title}
      </Text>
    </Pressable>
  );
}
function Field({
  value,
  onChange,
  placeholder,
  multiline = false,
  secure = false,
}: {
  value: string;
  onChange: (s: string) => void;
  placeholder: string;
  multiline?: boolean;
  secure?: boolean;
}) {
  const theme = useContext(ThemeContext);
  return (
    <TextInput
      accessibilityLabel={placeholder}
      placeholder={placeholder}
      placeholderTextColor={theme.muted}
      value={value}
      onChangeText={onChange}
      multiline={multiline}
      secureTextEntry={secure}
      autoCapitalize={secure ? "none" : "sentences"}
      style={[
        styles.input,
        {
          color: theme.text,
          borderColor: theme.border,
          backgroundColor: theme.card,
          minHeight: multiline ? 90 : 48,
        },
      ]}
    />
  );
}
function Card({ children }: { children: React.ReactNode }) {
  const theme = useContext(ThemeContext);
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: theme.card, borderColor: theme.border },
      ]}
    >
      {children}
    </View>
  );
}
function Label({
  children,
  big = false,
}: {
  children: React.ReactNode;
  big?: boolean;
}) {
  const theme = useContext(ThemeContext);
  return (
    <Text
      style={{
        color: theme.text,
        fontSize: big ? 22 : 15,
        fontWeight: big ? "700" : "400",
        marginBottom: 8,
      }}
    >
      {children}
    </Text>
  );
}
function Hint({ children }: { children: React.ReactNode }) {
  const theme = useContext(ThemeContext);
  return (
    <Text style={{ color: theme.muted, fontSize: 13, marginBottom: 8 }}>
      {children}
    </Text>
  );
}
function Navigation({
  tab,
  choose,
  tablet,
}: {
  tab: Tab;
  choose: (tab: Tab) => void;
  tablet: boolean;
}) {
  const theme = useContext(ThemeContext);
  return (
    <View
      style={[
        tablet ? styles.side : styles.nav,
        { backgroundColor: theme.card, borderColor: theme.border },
      ]}
    >
      {TABS.map((t) => (
        <Pressable
          accessibilityRole="tab"
          accessibilityState={{ selected: tab === t }}
          key={t}
          onPress={() => choose(t)}
          style={[
            styles.tab,
            {
              flex: tablet ? 0 : 1,
              borderBottomWidth: tab === t ? 3 : 0,
              borderColor: theme.accent,
            },
          ]}
        >
          <Feather
            name={
              (
                {
                  Hoje: "grid",
                  Clientes: "users",
                  Pedidos: "shopping-bag",
                  Maleta: "briefcase",
                  Pendências: "check-square",
                } as const
              )[t]
            }
            size={21}
            color={tab === t ? theme.text : theme.muted}
            style={{ marginBottom: 6 }}
          />
          <Text
            style={{
              color: theme.text,
              fontWeight: tab === t ? "700" : "400",
              fontSize: tablet ? 16 : 12,
            }}
          >
            {t}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}
export default function App() {
  const dark = useColorScheme() === "dark";
  const theme = {
    bg: dark ? "#102c33" : "#f1f5f5",
    card: dark ? "#173c44" : "#ffffff",
    text: dark ? "#f4f7f7" : "#153b43",
    muted: dark ? "#b8cecf" : "#526d73",
    border: dark ? "#31535a" : "#dce7e7",
    accent: "#b48c45",
  };
  return (
    <SafeAreaProvider>
      <ThemeContext.Provider value={theme}>
        <Mobile />
      </ThemeContext.Provider>
    </SafeAreaProvider>
  );
}
function Mobile() {
  const dark = useColorScheme() === "dark";
  const tablet = useWindowDimensions().width >= 720;
  const theme = {
    bg: dark ? "#102c33" : "#f1f5f5",
    card: dark ? "#173c44" : "#ffffff",
    text: dark ? "#f4f7f7" : "#153b43",
    muted: dark ? "#b8cecf" : "#526d73",
    border: dark ? "#31535a" : "#dce7e7",
    accent: "#b48c45",
  };
  const [session, setSession] = useState<SavedSession | null>(null),
    [store, setStore] = useState<LocalStore | null>(null);
  const [s, setSnapshot] = useState<Snapshot>(emptySnapshot()),
    [queue, setQueue] = useState<Pending[]>([]);
  const [tab, setTab] = useState<Tab>("Hoje"),
    [client, setClient] = useState<Entity | null>(null),
    [query, setQuery] = useState("");
  const [username, setUsername] = useState(""),
    [password, setPassword] = useState(""),
    [newPassword, setNewPassword] = useState(""),
    [base, setBase] = useState(DEFAULT_BASE);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [online, setOnline] = useState(false),
    [syncing, setSyncing] = useState(false);
  const [brand, setBrand] = useState(""),
    [cart, setCart] = useState<Entity[]>([]),
    [seller, setSeller] = useState(""),
    [orderNotes, setOrderNotes] = useState("");
  const [visit, setVisit] = useState<Entity | null>(null),
    [visitNotes, setVisitNotes] = useState(""),
    [checks, setChecks] = useState<Record<string, boolean>>({}),
    [nextAction, setNextAction] = useState(""),
    [result, setResult] = useState("Proposta enviada");
  const [taskText, setTaskText] = useState(""),
    [taskDate, setTaskDate] = useState(workDate()),
    [taskTime, setTaskTime] = useState("09:00");
  const [closing, setClosing] = useState({
      summary: "",
      objections: "",
      requests: "",
      nextPriority: "",
    }),
    [closingOpen, setClosingOpen] = useState(false);
  const [message, setMessage] = useState(""),
    [showHistory, setShowHistory] = useState(false);
  const engine = useRef<SyncEngine | null>(null);
  const [note, setNote] = useState(""),
    [qrLink, setQrLink] = useState(""),
    [generatedQR, setGeneratedQR] = useState(""),
    [utilityText, setUtilityText] = useState(""),
    [extracted, setExtracted] = useState("");
  const persistChain = useRef(Promise.resolve());
  const active = useRef(true);
  const now = workDate();
  const feedback = (e: unknown) =>
    setError(e instanceof Error ? e.message : "Não foi possível concluir.");
  async function reload(st = store) {
    if (!st) return;
    const [view, pending] = await Promise.all([st.view(), st.pending()]);
    setSnapshot(view);
    setClient((current) =>
      current ? view.clients.find((c) => c.id === current.id) || null : null,
    );
    setQueue(pending);
  }
  async function sync() {
    if (!engine.current) return;
    setSyncing(true);
    setError("");
    try {
      await engine.current.run();
      await reload();
    } catch (e) {
      feedback(e);
    } finally {
      setSyncing(false);
    }
  }
  async function activate(saved: SavedSession) {
    if (saved.offlineUntil < Date.now())
      throw new Error("Acesso offline expirou. Entre novamente com internet.");
    const local = await openStore(saved);
    setSession(saved);
    setStore(local);
    setSeller(saved.user);
    setBase(saved.base);
    const api = new L2API(saved.base, saved.token);
    engine.current = new SyncEngine(local, api, id, () => {
      if (active.current) void reload(local).catch(feedback);
    });
    await reload(local);
    const draft = await local.readDraft<any>("cart");
    if (draft) {
      setCart(draft.cart || []);
      setClient(draft.client || null);
      setSeller(draft.seller || saved.user);
      setOrderNotes(draft.notes || "");
    }
    const v = await local.readDraft<any>("visit");
    if (v) {
      setVisit(v.visit);
      setVisitNotes(v.notes || "");
      setChecks(v.checks || {});
      setNextAction(v.next || "");
      setResult(v.result || "Proposta enviada");
    }
    const c = await local.readDraft<any>("closing:" + now);
    if (c) setClosing(c);
    await registerBackground().catch(() => {});
  }
  useEffect(() => {
    active.current = true;
    void restored()
      .then((saved) => saved && activate(saved))
      .catch(feedback);
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    const unsub = NetInfo.addEventListener((state) => {
      const connected =
        !!state.isConnected && state.isInternetReachable !== false;
      setOnline(connected);
      if (connected && engine.current) void sync();
    });
    const app = AppState.addEventListener("change", (state) => {
      if (state === "active") void sync();
    });
    return () => {
      unsub();
      app.remove();
    };
  }, [store]);
  useEffect(() => {
    if (!session || !online) return;
    const es = new EventSource<"records" | "ready">(
      session.base + "/api/records/events",
      {
        headers: { Authorization: "Bearer " + session.token },
        pollingInterval: 10000,
      },
    );
    let timer: ReturnType<typeof setTimeout> | undefined;
    const update = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void sync(), 500);
    };
    es.addEventListener("records", update);
    es.addEventListener("ready", update);
    const interval = setInterval(() => void sync(), 30000);
    return () => {
      es.close();
      clearInterval(interval);
      if (timer) clearTimeout(timer);
    };
  }, [session?.token, online, store]);
  const autosave = (key: string, value: any) => {
    if (!store) return;
    persistChain.current = persistChain.current
      .then(() => store.draft(key, value))
      .catch(feedback);
  };
  const updateCart = (
    items: Entity[],
    selected = client,
    selectedSeller = seller,
    notes = orderNotes,
  ) => {
    setCart(items);
    autosave("cart", {
      cart: items,
      client: selected,
      seller: selectedSeller,
      notes,
    });
  };
  async function login() {
    setBusy(true);
    setError("");
    try {
      const api = new L2API(base);
      const login = await api.login(username.trim(), password);
      if (login.mustChangePassword) {
        if (newPassword.length < 12)
          throw new Error(
            "Sua senha é provisória. Informe uma nova senha com 12 caracteres ou mais.",
          );
        await api.changePassword(password, newPassword);
        throw new Error(
          "Senha atualizada. Entre novamente usando a nova senha.",
        );
      }
      const snapshot = await api.snapshot(); // Fail early if guarded routes not installed; never use unsafe legacy writes.
      const saved = {
        ...login,
        base: api.base,
        offlineUntil: Date.now() + 72 * 3600000,
      };
      const st = await openStore(saved);
      await st.apply(snapshot);
      await remember(saved);
      await activate(saved);
      setPassword("");
      setNewPassword("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 404)
        setError(
          "A integração mobile precisa ser instalada no servidor L2 One antes deste acesso.",
        );
      else feedback(e);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    await persistChain.current;
    Alert.alert(
      "Sair da conta",
      queue.length
        ? `${queue.length} registros pendentes ficarão protegidos neste aparelho para a mesma conta.`
        : "Os dados locais continuarão protegidos para esta conta.",
      [
        { text: "Continuar trabalhando", style: "cancel" },
        {
          text: "Sair",
          onPress: () =>
            void (async () => {
              await new L2API(session!.base, session!.token)
                .logout()
                .catch(() => {});
              await forget();
              engine.current = null;
              setSession(null);
              setStore(null);
              setSnapshot(emptySnapshot());
              setQueue([]);
              setClient(null);
              setCart([]);
              setVisit(null);
              setClosingOpen(false);
              setMessage("");
            })().catch(feedback),
        },
      ],
    );
  }
  async function enqueue(kind: any, data: Entity) {
    if (!store || !session) return;
    if (session.offlineUntil < Date.now())
      throw new Error("Entre novamente para renovar o acesso offline.");
    await store.queue(kind, data, id());
    await reload();
    if (online) void sync();
  }
  async function startVisit() {
    if (!client || !session) return;
    if (visit) {
      Alert.alert(
        "Visita em andamento",
        "Conclua a visita atual antes de iniciar outra.",
      );
      return;
    }
    const v = {
      id: id(),
      clientId: client.id,
      user: session.user,
      date: now,
      checkIn: new Date().toISOString(),
      checklistVersion: "l2-mobile-1",
    };
    setVisit(v);
    setChecks({});
    setVisitNotes("");
    setNextAction("");
    autosave("visit", { visit: v, checks: {}, notes: "", next: "", result });
    setTab("Clientes");
  }
  async function locate() {
    if (!visit) return;
    const permission = await Location.requestForegroundPermissionsAsync();
    if (permission.status !== "granted")
      throw new Error(
        "Localização não autorizada. A visita pode continuar sem GPS.",
      );
    const point = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.Balanced,
    });
    const updated = {
      ...visit,
      latitude: point.coords.latitude,
      longitude: point.coords.longitude,
    };
    setVisit(updated);
    autosave("visit", {
      visit: updated,
      checks,
      notes: visitNotes,
      next: nextAction,
      result,
    });
  }
  async function completeVisit() {
    if (!visit) return;
    if (!nextAction.trim())
      throw new Error("Registre a próxima ação para concluir a visita.");
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(taskDate) ||
      new Date(taskDate + "T12:00:00Z").toISOString().slice(0, 10) !== taskDate
    )
      throw new Error("Informe uma data de retorno válida.");
    const completed = {
      ...visit,
      checkOut: new Date().toISOString(),
      returnDate: taskDate,
      result,
      notes: visitNotes,
      next: nextAction,
      checklist: CHECKS.map((text) => ({ text, done: !!checks[text] })),
    };
    await enqueue("visit", completed);
    await persistChain.current;
    await store!.removeDraft("visit");
    setVisit(null);
    setChecks({});
    Alert.alert(
      "Visita salva",
      "O registro será confirmado pelo L2 One após sincronização.",
    );
  }
  async function submitOrder() {
    if (!client || !session) throw new Error("Selecione o cliente.");
    const order = makeOrder(
      id(),
      client,
      seller,
      cart,
      session.user,
      orderNotes,
    );
    await enqueue("order", order);
    await persistChain.current;
    await store!.removeDraft("cart");
    setCart([]);
    setOrderNotes("");
    Alert.alert(
      "Pedido salvo no aparelho",
      "O preço será conferido antes de enviar ao L2 One. Veja a confirmação em Pendências e Pedidos.",
    );
  }
  async function reviewRejectedOrder(q: Pending) {
    const c = s.clients.find((c) => c.id === q.data.clientId);
    if (!c) throw new Error("Cliente não está mais na carteira autorizada.");
    if (s.orders.some((o) => o.id === q.data.id))
      throw new Error(
        "Pedido já existe no L2 One. Consulte o original para evitar duplicação.",
      );
    const items = q.data.items.map((line: Entity) => {
      const p = s.prices.find(
        (p) =>
          p.sku === line.sku &&
          p.brand === line.brand &&
          (p.tableId || p.brand + "|" + p.state) ===
            (line.tableId || line.brand + "|" + line.state),
      );
      if (!p)
        throw new Error("Produto não disponível na tabela atual: " + line.sku);
      return { ...line, unitPrice: String(p.price) };
    });
    await store!.draft("cart", {
      cart: items,
      client: c,
      seller: q.data.sellerResponsible,
      notes: q.data.notes || "",
    });
    await store!.resolve(q.changeId, null, id());
    setClient(c);
    setSeller(q.data.sellerResponsible);
    setOrderNotes(q.data.notes || "");
    setCart(items);
    setTab("Pedidos");
    await reload();
  }
  async function addTask() {
    if (!session || taskText.trim().length < 3)
      throw new Error("Descreva a tarefa com pelo menos 3 caracteres.");
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(taskDate) ||
      !/^([01]\d|2[0-3]):[0-5]\d$/.test(taskTime)
    )
      throw new Error("Use data AAAA-MM-DD e horário HH:MM.");
    await enqueue("task", {
      id: id(),
      clientId: client?.id || "",
      user: session.user,
      text: taskText.trim(),
      date: taskDate,
      time: taskTime,
      type: "Retorno",
      status: "Aberta",
      source: "field_agenda",
    });
    setTaskText("");
  }
  async function closeDay() {
    if (!session || !closing.summary.trim())
      throw new Error("Preencha o resumo do dia.");
    const revisions = s.closings
      .filter((c) => c.user === session.user && c.workDate === now)
      .map((c) => Number(c.revision) || 1);
    const revision = Math.max(0, ...revisions) + 1;
    await enqueue("closing", {
      id: id(),
      workDate: now,
      revision,
      ...closing,
      metrics: dailyMetrics(s, session.user),
      localPending: queue.length,
    });
    setClosingOpen(false);
    Alert.alert(
      "Fechamento salvo",
      "O relatório ficará disponível na consulta gerencial mobile após sincronização.",
    );
  }
  async function share() {
    if (!client || !message.trim())
      throw new Error("Selecione cliente e mensagem.");
    const url =
      "https://wa.me/" +
      phone(client.phone) +
      "?text=" +
      encodeURIComponent(message);
    await Linking.openURL(url);
    await enqueue("interaction", {
      id: id(),
      clientId: client.id,
      owner: session!.user,
      type: "WhatsApp",
      text:
        "Compartilhamento aberto (envio não confirmado): " +
        message.slice(0, 1800),
      at: new Date().toISOString(),
    });
  }
  const action = (fn: () => Promise<any>) => () => {
    setError("");
    void fn().catch(feedback);
  };
  const filteredClients = s.clients.filter((c) =>
    (
      c.name +
      " " +
      (c.tradeName || "") +
      " " +
      (c.taxId || "") +
      " " +
      (c.city || "")
    )
      .toLocaleLowerCase()
      .includes(query.toLocaleLowerCase()),
  );
  const selectClient = (c: Entity) => {
    setClient(c);
    setShowHistory(false);
    setMessage("");
    setNote("");
    void store
      ?.readDraft<string>("note:" + c.id)
      .then((v) => setNote(v || ""))
      .catch(feedback);
    if (cart.length) updateCart(cart, c);
  };
  const clientList = (
    <FlatList
      data={filteredClients}
      keyExtractor={(c) => c.id}
      keyboardShouldPersistTaps="handled"
      ListEmptyComponent={
        <Hint>Sem clientes disponíveis. Sincronize sua carteira.</Hint>
      }
      renderItem={({ item: c }) => (
        <Pressable
          accessibilityRole="button"
          onPress={() => selectClient(c)}
          style={[
            styles.clientRow,
            {
              backgroundColor: client?.id === c.id ? theme.card : "transparent",
              borderColor: theme.border,
            },
          ]}
        >
          <Text style={{ color: theme.text, fontWeight: "700" }}>
            {c.tradeName || c.name}
          </Text>
          <Text style={{ color: theme.muted }}>
            {[c.city, c.neighborhood || c.district, c.state]
              .filter(Boolean)
              .join(" • ")}
          </Text>
        </Pressable>
      )}
    />
  );
  const visitForm = visit && (
    <Card>
      <Label big>Visita em andamento</Label>
      <Hint>
        Cliente:{" "}
        {s.clients.find((c) => c.id === visit.clientId)?.name || visit.clientId}
      </Hint>
      {CHECKS.map((text) => (
        <Pressable
          accessibilityRole="checkbox"
          accessibilityState={{ checked: !!checks[text] }}
          key={text}
          onPress={() => {
            const next = { ...checks, [text]: !checks[text] };
            setChecks(next);
            autosave("visit", {
              visit,
              checks: next,
              notes: visitNotes,
              next: nextAction,
              result,
            });
          }}
          style={styles.check}
        >
          <Text style={{ color: theme.text }}>
            {checks[text] ? "✓" : "○"} {text}
          </Text>
        </Pressable>
      ))}
      <Field
        value={visitNotes}
        placeholder="Observações da visita"
        multiline
        onChange={(v) => {
          setVisitNotes(v);
          autosave("visit", {
            visit,
            checks,
            notes: v,
            next: nextAction,
            result,
          });
        }}
      />
      <Field
        value={nextAction}
        placeholder="Próxima ação obrigatória"
        onChange={(v) => {
          setNextAction(v);
          autosave("visit", {
            visit,
            checks,
            notes: visitNotes,
            next: v,
            result,
          });
        }}
      />
      <View style={styles.row}>
        {["Pedido", "Proposta enviada", "Retorno", "Não atendido"].map((r) => (
          <Button
            key={r}
            title={r}
            secondary={result !== r}
            onPress={() => {
              setResult(r);
              autosave("visit", {
                visit,
                checks,
                notes: visitNotes,
                next: nextAction,
                result: r,
              });
            }}
          />
        ))}
      </View>
      <Field
        value={taskDate}
        onChange={setTaskDate}
        placeholder="Data de retorno AAAA-MM-DD"
      />
      <Button
        title="Registrar localização (opcional)"
        secondary
        onPress={action(locate)}
      />
      <Button title="Concluir visita" onPress={action(completeVisit)} />
    </Card>
  );
  const customer = client ? (
    <>
      <Card>
        <Label big>{client.tradeName || client.name}</Label>
        <Hint>
          {client.taxId} • {client.state}
        </Hint>
        <Label>
          {[client.address, client.neighborhood, client.city]
            .filter(Boolean)
            .join(", ")}
        </Label>
        <View style={styles.row}>
          <Button title="Iniciar visita" onPress={action(startVisit)} />
          <Button
            title="Novo pedido"
            onPress={() => {
              setTab("Pedidos");
              setQuery("");
            }}
          />
          <Button
            title="Histórico"
            secondary
            onPress={() => setShowHistory(!showHistory)}
          />
          <Button title="WhatsApp" secondary onPress={() => setTab("Maleta")} />
        </View>
      </Card>
      {showHistory && (
        <Card>
          <Label big>Histórico</Label>
          {[
            ...s.orders.filter((o) => o.clientId === client.id),
            ...s.visits.filter((v) => v.clientId === client.id),
            ...s.interactions.filter((v) => v.clientId === client.id),
          ]
            .sort((a, b) =>
              String(b.date || b.at).localeCompare(String(a.date || a.at)),
            )
            .slice(0, 40)
            .map((x) => (
              <View key={x.id} style={styles.history}>
                <Label>
                  {x.date || x.at} • {x.status || x.result || x.type}
                </Label>
                <Hint>
                  {x.notes ||
                    x.text ||
                    (x.amount
                      ? currency(Math.round(Number(x.amount) * 100))
                      : "")}
                </Hint>
              </View>
            ))}
        </Card>
      )}
      {visitForm}
    </>
  ) : (
    <Hint>
      Selecione um cliente para consultar histórico ou começar atendimento.
    </Hint>
  );
  if (!session)
    return (
      <SafeAreaView style={[styles.root, { backgroundColor: theme.bg }]}>
        <KeyboardAvoidingView
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          style={{ flex: 1 }}
        >
          <ScrollView
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={[
              styles.content,
              { maxWidth: 520, alignSelf: "center", width: "100%" },
            ]}
          >
            <Image
              source={require("./assets/logo-official.png")}
              resizeMode="contain"
              style={{
                width: 200,
                height: 134,
                alignSelf: "center",
                borderRadius: 16,
              }}
            />
            <Label big>L2 Produtividade</Label>
            <Hint>Maleta Digital • Pará e Amapá</Hint>
            <Card>
              <Label>Entre com sua conta do L2 One</Label>
              <Field
                value={username}
                onChange={setUsername}
                placeholder="Usuário"
              />
              <Field
                value={password}
                onChange={setPassword}
                placeholder="Senha"
                secure
              />
              <Field
                value={newPassword}
                onChange={setNewPassword}
                placeholder="Nova senha (somente primeiro acesso)"
                secure
              />
              <Field
                value={base}
                onChange={setBase}
                placeholder="Servidor HTTPS L2 One"
              />
              <Button
                title={busy ? "Entrando…" : "Entrar"}
                onPress={() => void login()}
                disabled={busy}
              />
              <Hint>A primeira sincronização precisa de internet.</Hint>
            </Card>
            {!!error && (
              <Text accessibilityRole="alert" style={{ color: theme.text }}>
                {error}
              </Text>
            )}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    );
  const metrics = dailyMetrics(s, session.user);
  const route = s.routes.filter(
    (r) => r.user === session.user && r.date === now,
  );
  const products = s.prices.filter(
    (p) =>
      (!client || p.state === client.state || p.state === "ALL") &&
      (!brand || p.brand === brand) &&
      ((p.description || p.name || "") + " " + p.sku)
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
  );
  const content =
    tab === "Hoje" ? (
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <Label big>Bom trabalho, {session.user}</Label>
        <Hint>
          {now} • {online ? "Conectado" : "Offline"} • {queue.length} pendências
        </Hint>
        <View style={styles.row}>
          <Card>
            <Feather
              name="map-pin"
              size={20}
              color={theme.accent}
              style={{ marginBottom: 12 }}
            />
            <Label big>{metrics.visits}</Label>
            <Hint>Visitas concluídas</Hint>
          </Card>
          <Card>
            <Feather
              name="trending-up"
              size={20}
              color={theme.accent}
              style={{ marginBottom: 12 }}
            />
            <Label big>{currency(metrics.confirmed)}</Label>
            <Hint>Pedidos no L2 One hoje</Hint>
          </Card>
          <Card>
            <Feather
              name="award"
              size={20}
              color={theme.accent}
              style={{ marginBottom: 12 }}
            />
            <Label big>{metrics.clients}</Label>
            <Hint>Clientes com pedidos</Hint>
          </Card>
        </View>
        <Card>
          <Label big>Metas do mês</Label>
          {s.goals
            .filter(
              (g) =>
                g.month === now.slice(0, 7) &&
                (g.user === session.user || g.user === "Equipe"),
            )
            .map((g) => {
              const total = s.orders
                .filter(
                  (o) =>
                    o.date?.startsWith(now.slice(0, 7)) &&
                    (g.user === "Equipe" ||
                      (o.sellerResponsible || o.user) === session.user) &&
                    (o.brand === g.brand || !g.brand),
                )
                .reduce((a, o) => a + Number(o.amount || 0), 0);
              return (
                <View key={g.id} style={{ marginBottom: 16 }}>
                  <Hint>
                    {g.brand || "Total"}: {currency(Math.round(total * 100))} /{" "}
                    {currency(Math.round(Number(g.amount || 0) * 100))}
                  </Hint>
                  <View
                    style={{
                      height: 7,
                      borderRadius: 10,
                      backgroundColor: theme.border,
                      overflow: "hidden",
                    }}
                  >
                    <View
                      style={{
                        height: 7,
                        width: (Math.min(
                          100,
                          Number(g.amount) > 0
                            ? (total / Number(g.amount)) * 100
                            : 0,
                        ) + "%") as any,
                        backgroundColor: theme.accent,
                        borderRadius: 10,
                      }}
                    />
                  </View>
                </View>
              );
            })}
          <Hint>
            Metas usam pedidos registrados. Faturamento requer confirmação
            própria do ERP.
          </Hint>
        </Card>
        <Card>
          <Label big>Rota do dia</Label>
          {route.length ? (
            route.map((r) => {
              const c = s.clients.find((c) => c.id === r.clientId);
              return (
                <Button
                  key={r.id}
                  title={`${r.status === "Atendido" ? "✓ " : ""}${c?.name || "Cliente indisponível"}`}
                  secondary
                  onPress={() => {
                    if (c) selectClient(c);
                    setTab("Clientes");
                  }}
                />
              );
            })
          ) : (
            <Hint>Sem rota agendada. Consulte sua carteira.</Hint>
          )}
        </Card>
        <View style={styles.row}>
          <Button title="Carteira" onPress={() => setTab("Clientes")} />
          <Button
            title="Fechamento do dia"
            onPress={() => setClosingOpen(!closingOpen)}
          />
        </View>
        {closingOpen && (
          <Card>
            <Label big>Diário de bordo</Label>
            {(
              [
                ["summary", "Resumo e vitórias"],
                ["objections", "Objeções do mercado"],
                ["requests", "Solicitações ao escritório"],
                ["nextPriority", "Prioridade de amanhã"],
              ] as const
            ).map(([k, label]) => (
              <Field
                key={k}
                value={closing[k]}
                placeholder={label}
                multiline
                onChange={(v) => {
                  const updated = { ...closing, [k]: v };
                  setClosing(updated);
                  autosave("closing:" + now, updated);
                }}
              />
            ))}
            <Button title="Concluir fechamento" onPress={action(closeDay)} />
          </Card>
        )}
        <Card>
          <Label>Fechamentos sincronizados</Label>
          {s.closings.slice(0, 10).map((c) => (
            <Hint key={c.id}>
              {c.workDate} • {c.user} • revisão {c.revision}: {c.summary}
            </Hint>
          ))}
        </Card>
      </ScrollView>
    ) : tab === "Clientes" ? (
      <View style={[styles.content, { flex: 1 }]}>
        <Field
          value={query}
          onChange={setQuery}
          placeholder="Buscar nome, CNPJ ou cidade"
        />
        <View style={{ flex: 1, flexDirection: tablet ? "row" : "column" }}>
          {(!client || tablet) && (
            <View style={{ flex: tablet ? 0.8 : 1 }}>{clientList}</View>
          )}
          <ScrollView style={{ flex: 1.2 }} keyboardShouldPersistTaps="handled">
            {client && !tablet && (
              <Button
                title="Voltar à carteira"
                secondary
                onPress={() => setClient(null)}
              />
            )}{" "}
            {customer}
          </ScrollView>
        </View>
      </View>
    ) : tab === "Pedidos" ? (
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <Label big>Pedido multimarcas</Label>
        <Hint>
          {client
            ? `Cliente: ${client.name} • ${client.state}`
            : "Selecione o cliente na aba Clientes."}
        </Hint>
        <Button
          title="Selecionar cliente"
          secondary
          onPress={() => setTab("Clientes")}
        />
        {["Administrativo", "Administradora", "Gestão"].includes(
          session.role,
        ) || session.user === "Euler" ? (
          <View style={styles.row}>
            {s.sellers.map((name) => (
              <Button
                key={name}
                title={name}
                secondary={seller !== name}
                onPress={() => {
                  setSeller(name);
                  updateCart(cart, client, name);
                }}
              />
            ))}
          </View>
        ) : (
          <Hint>Vendedor: {seller}</Hint>
        )}
        <Field
          value={query}
          onChange={setQuery}
          placeholder="Buscar produto ou SKU"
        />
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ gap: 8, alignItems: "center" }}
        >
          <Button
            title="Todas as marcas"
            secondary
            onPress={() => setBrand("")}
          />
          {s.industries
            .filter((b) => b.active !== false)
            .map((b) => (
              <Button
                key={b.id}
                title={b.name || b.id}
                secondary={brand !== b.name}
                onPress={() => setBrand(b.name || b.id)}
              />
            ))}
        </ScrollView>
        <Hint>
          Preços da última sincronização: {s.asOf?.slice(0, 16) || "sem dados"}.
          Estoque não informado pela fonte atual.
        </Hint>
        {products.slice(0, 60).map((p) => (
          <Card key={p.id || p.brand + "|" + p.sku + "|" + p.state}>
            <Label>{p.description || p.name || p.sku}</Label>
            <Hint>
              {p.brand} • SKU {p.sku} • {currency(cents(p.price))}
            </Hint>
            <Button
              title="Adicionar ao pedido"
              secondary
              disabled={!client}
              onPress={() => {
                const key =
                  (p.tableId || p.brand + "|" + p.state) + "|" + p.sku;
                const existing = cart.find((i) => i.id === key);
                updateCart(
                  existing
                    ? cart.map((i) =>
                        i.id === key ? { ...i, quantity: i.quantity + 1 } : i,
                      )
                    : [
                        ...cart,
                        {
                          ...p,
                          id: key,
                          brand: p.brand,
                          sku: String(p.sku),
                          unitPrice: String(p.price),
                          quantity: 1,
                          state: p.state === "ALL" ? client!.state : p.state,
                        },
                      ],
                );
              }}
            />
          </Card>
        ))}
        {products.length > 60 && (
          <Hint>Refine a busca para ver outros produtos.</Hint>
        )}
        <Card>
          <Label big>Carrinho • {cart.length} itens</Label>
          {cart.map((i) => (
            <View key={i.id} style={styles.history}>
              <Label>{i.description || i.sku}</Label>
              <View style={styles.row}>
                <Button
                  title="−"
                  secondary
                  onPress={() =>
                    updateCart(
                      cart.flatMap((x) =>
                        x.id !== i.id
                          ? [x]
                          : x.quantity > 1
                            ? [{ ...x, quantity: x.quantity - 1 }]
                            : [],
                      ),
                    )
                  }
                />
                <Label>
                  {i.quantity} un. • {currency(cents(i.unitPrice) * i.quantity)}
                </Label>
                <Button
                  title="+"
                  secondary
                  onPress={() =>
                    updateCart(
                      cart.map((x) =>
                        x.id === i.id ? { ...x, quantity: x.quantity + 1 } : x,
                      ),
                    )
                  }
                />
              </View>
            </View>
          ))}
          <Label big>{currency(orderTotal(cart))}</Label>
          <Field
            value={orderNotes}
            placeholder="Observações e condições solicitadas"
            multiline
            onChange={(v) => {
              setOrderNotes(v);
              updateCart(cart, client, seller, v);
            }}
          />
          <Hint>
            Condições especiais precisam de aprovação no ERP. Não aplicar
            descontos sem regra cadastrada.
          </Hint>
          <Button
            title="Salvar e enviar pedido"
            disabled={!cart.length || !client}
            onPress={action(submitOrder)}
          />
        </Card>
        <Card>
          <Label big>Pedidos no L2 One</Label>
          {s.orders
            .filter((o) => !client || o.clientId === client.id)
            .slice(-20)
            .reverse()
            .map((o) => (
              <Hint key={o.id}>
                #{o.orderNumber || o.id.slice(0, 8)} • {o.status} •{" "}
                {currency(Math.round(Number(o.amount || 0) * 100))}
              </Hint>
            ))}
        </Card>
      </ScrollView>
    ) : tab === "Maleta" ? (
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <Label big>Maleta comercial</Label>
        <Hint>Cliente: {client?.name || "selecione na carteira"}</Hint>
        <Button
          title="Selecionar cliente"
          secondary
          onPress={() => setTab("Clientes")}
        />
        {[
          ...initialSnippets,
          ...s.whatsappTemplates.map((t) => ({
            id: t.id,
            title: t.title || t.name || "Modelo comercial",
            body: t.body || t.text || t.message || "",
          })),
        ].map((t) => (
          <Button
            key={t.id}
            title={t.title}
            secondary
            onPress={() => {
              try {
                if (!client) throw new Error("Selecione um cliente.");
                setMessage(template(t.body, client, session.user, brand, s));
              } catch (e) {
                feedback(e);
              }
            }}
          />
        ))}
        <Field
          value={message}
          onChange={setMessage}
          placeholder="Revise a mensagem antes de compartilhar"
          multiline
        />
        <View style={styles.row}>
          <Button
            title="Copiar mensagem"
            secondary
            onPress={action(() => Clipboard.setStringAsync(message))}
          />
          <Button title="Abrir WhatsApp" onPress={action(share)} />
        </View>
        <Hint>
          Abertura não confirma envio nem entrega. O contato será registrado com
          essa distinção.
        </Hint>
        <Card>
          <Label big>Catálogos por marca</Label>
          <Hint>
            Baixe os PDFs antes de sair para a rota. A cópia fica protegida no
            banco local e pode ser compartilhada sem conexão.
          </Hint>
          {client &&
            s.priceTables
              .filter(
                (t) =>
                  t.active !== false &&
                  (t.state === client.state || t.state === "ALL"),
              )
              .map((t) => (
                <View key={t.id} style={styles.history}>
                  <Label>{t.title || t.brand}</Label>
                  <View style={styles.row}>
                    <Button
                      title="Baixar PDF atualizado"
                      disabled={!online}
                      secondary
                      onPress={action(async () => {
                        await downloadCatalog(store!, session, client.id, t.id);
                        Alert.alert(
                          "PDF baixado",
                          "Disponível para compartilhar offline.",
                        );
                      })}
                    />
                    <Button
                      title="Compartilhar PDF baixado"
                      onPress={action(() =>
                        shareCatalog(store!, client.id, t.id),
                      )}
                    />
                  </View>
                </View>
              ))}
        </Card>
        <Card>
          <Label big>Nota rápida do atendimento</Label>
          <Field
            value={note}
            placeholder="Anote ideias, objeções ou combinados"
            multiline
            onChange={(v) => {
              setNote(v);
              if (client) autosave("note:" + client.id, v);
            }}
          />
          <Button
            title="Registrar nota no histórico"
            disabled={!client || !note.trim()}
            onPress={action(() =>
              enqueue("interaction", {
                id: id(),
                clientId: client!.id,
                owner: session.user,
                type: "Conversa",
                text: note.slice(0, 2000),
                at: new Date().toISOString(),
              }),
            )}
          />
        </Card>
        <Card>
          <Label big>QR Code de catálogo</Label>
          <Field
            value={qrLink}
            placeholder="Link público HTTPS do catálogo"
            onChange={setQrLink}
          />
          <Button
            title="Gerar QR Code"
            secondary
            onPress={() => {
              try {
                setGeneratedQR(catalogLink(qrLink));
              } catch (e) {
                feedback(e);
              }
            }}
          />
          {!!generatedQR && <QRView value={generatedQR} />}
        </Card>
        <Card>
          <Label big>Organizar texto e extrair dados</Label>
          <Field
            value={utilityText}
            placeholder="Cole o texto recebido do comprador"
            multiline
            onChange={setUtilityText}
          />
          <View style={styles.row}>
            <Button
              title="Formatar texto"
              secondary
              onPress={() => setUtilityText(cleanText(utilityText))}
            />
            <Button
              title="Extrair CNPJ, e-mail e SKU"
              secondary
              onPress={() =>
                setExtracted(JSON.stringify(extractText(utilityText), null, 2))
              }
            />
            <Button
              title="Copiar texto limpo"
              secondary
              onPress={action(() => Clipboard.setStringAsync(utilityText))}
            />
          </View>
          {!!extracted && <Hint>{extracted}</Hint>}
          <Hint>
            Confira os dados extraídos. Nenhum cadastro é alterado
            automaticamente.
          </Hint>
        </Card>
      </ScrollView>
    ) : (
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        <Label big>Pendências e próximas ações</Label>
        <Card>
          <Field
            value={taskText}
            onChange={setTaskText}
            placeholder="Próxima ação / tarefa"
          />
          <Field
            value={taskDate}
            onChange={setTaskDate}
            placeholder="Data AAAA-MM-DD"
          />
          <Field
            value={taskTime}
            onChange={setTaskTime}
            placeholder="Horário HH:MM"
          />
          <Hint>
            {client
              ? "Ligada a " + client.name
              : "Tarefa pessoal do responsável"}
          </Hint>
          <Button title="Salvar tarefa" onPress={action(addTask)} />
        </Card>
        {s.tasks
          .filter((t) => t.user === session.user && t.status !== "Concluída")
          .map((t) => (
            <Card key={t.id}>
              <Label>{t.text || t.title}</Label>
              <Hint>
                {t.date} • {t.time} • {t.status}
              </Hint>
              <Button
                title="Concluir tarefa"
                secondary
                onPress={action(() =>
                  enqueue("task", { ...t, status: "Concluída" }),
                )}
              />
            </Card>
          ))}
        <Label big>Fila de sincronização</Label>
        {queue.length === 0 && <Hint>Nenhum registro pendente.</Hint>}
        {queue.map((q) => (
          <Card key={q.changeId}>
            <Label>
              {q.type} • {q.data.id.slice(0, 8)}
            </Label>
            <Hint>
              {q.state === "pending"
                ? "Salvo no aparelho, aguardando confirmação"
                : q.state === "conflict"
                  ? "Conflito de versões"
                  : "Revisão necessária"}
            </Hint>
            <Hint>{q.error}</Hint>
            {q.type === "order" && (
              <Label>
                {currency(Math.round(Number(q.data.amount || 0) * 100))}
              </Label>
            )}
            {q.state === "conflict" && (
              <Hint>
                Versão remota: {JSON.stringify((q as any).remote).slice(0, 300)}
              </Hint>
            )}
            {q.state === "rejected" && q.type === "order" && (
              <Button
                title="Revisar com preços atuais no carrinho"
                secondary
                onPress={action(() => reviewRejectedOrder(q))}
              />
            )}
            {q.state === "conflict" && q.type !== "order" && (
              <Button
                title="Reenviar minha versão após revisar conflito"
                secondary
                onPress={() =>
                  Alert.alert(
                    "Revisar versões",
                    "Sua versão substituirá os campos remotos, após nova conferência do servidor.",
                    [
                      { text: "Cancelar", style: "cancel" },
                      {
                        text: "Reenviar minha versão",
                        onPress: action(async () => {
                          await store!.resolve(q.changeId, q.data, id());
                          await reload();
                          if (online) void sync();
                        }),
                      },
                    ],
                  )
                }
              />
            )}
            {q.state !== "pending" && (
              <Button
                title="Manter versão do L2 One e arquivar pendência"
                secondary
                onPress={() =>
                  Alert.alert(
                    "Resolver pendência",
                    "A versão local será arquivada. O registro do L2 One será mantido.",
                    [
                      { text: "Cancelar", style: "cancel" },
                      {
                        text: "Resolver",
                        onPress: action(async () => {
                          await store!.resolve(q.changeId, null, id());
                          await reload();
                        }),
                      },
                    ],
                  )
                }
              />
            )}
          </Card>
        ))}
      </ScrollView>
    );
  return (
    <SafeAreaView style={[styles.root, { backgroundColor: theme.bg }]}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <View style={[styles.header, { borderColor: theme.border }]}>
          <View>
            <Text
              style={{ color: theme.text, fontSize: 18, fontWeight: "700" }}
            >
              L2 Produtividade
            </Text>
            <Text style={{ color: theme.muted, fontSize: 11 }}>
              {syncing ? "Sincronizando…" : online ? "Conectado" : "Offline"} •{" "}
              {queue.length} pendências
            </Text>
          </View>
          <View style={styles.row}>
            <Button
              title="Sincronizar"
              secondary
              disabled={syncing}
              onPress={() => void sync()}
            />
            <Button title="Sair" secondary onPress={action(logout)} />
          </View>
        </View>
        {!!error && (
          <Pressable
            onPress={() => setError("")}
            style={[styles.error, { backgroundColor: theme.card }]}
          >
            <Text accessibilityRole="alert" style={{ color: theme.text }}>
              {error} • toque para fechar
            </Text>
          </Pressable>
        )}
        <View style={{ flex: 1, flexDirection: tablet ? "row" : "column" }}>
          {tablet && (
            <Navigation
              tab={tab}
              tablet={tablet}
              choose={(t) => {
                setTab(t);
                setQuery("");
              }}
            />
          )}
          <View style={{ flex: 1 }}>{content}</View>
          {!tablet && (
            <Navigation
              tab={tab}
              tablet={tablet}
              choose={(t) => {
                setTab(t);
                setQuery("");
              }}
            />
          )}
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}
const styles = StyleSheet.create({
  root: { flex: 1 },
  content: { padding: 16, gap: 12 },
  header: {
    padding: 12,
    borderBottomWidth: 1,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    flexWrap: "wrap",
    gap: 8,
  },
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8, alignItems: "center" },
  card: { padding: 16, borderRadius: 20, borderWidth: 1, marginBottom: 12 },
  button: {
    minHeight: 48,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderWidth: 1,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 8,
  },
  input: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    fontSize: 16,
    marginBottom: 12,
    textAlignVertical: "top",
  },
  nav: { flexDirection: "row", borderTopWidth: 1 },
  side: { width: 156, padding: 8, borderRightWidth: 1 },
  tab: {
    paddingVertical: 12,
    paddingHorizontal: 8,
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  clientRow: { minHeight: 64, padding: 12, borderBottomWidth: 1 },
  check: { minHeight: 48, paddingVertical: 14 },
  history: {
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderColor: "#80989d",
  },
  error: { padding: 14, margin: 8, borderRadius: 10 },
});
