interface SecretBinding { get(): Promise<string>; }
interface Env {
  RUNTIME_STATE: KVNamespace;
  UPSTOX_API_KEY: SecretBinding;
  UPSTOX_API_SECRET: SecretBinding;
  SCHWAB_APP_KEY: SecretBinding;
  SCHWAB_APP_SECRET: SecretBinding;
  TOKEN_ENCRYPTION_KEY: SecretBinding;
  MANAGEMENT_URL: string;
  SCHWAB_CALLBACK_URL: string;
}
interface UpstoxProfile { user_id?: string; user_name?: string; }
interface EncryptedToken {
  version: 1; iv: string; ciphertext: string; issued_at: string; expires_at: string; user_id: string; user_name: string;
}
interface EncryptedValue { iv: string; ciphertext: string; }
interface SchwabToken {
  version: 1;
  access_token: EncryptedValue;
  refresh_token: EncryptedValue;
  issued_at: string;
  access_expires_at: string;
  refresh_expires_at: string;
  token_type: string;
  scope: string;
}
interface SchwabTokenPayload {
  access_token?: string;
  refresh_token?: string;
  expires_in?: number;
  token_type?: string;
  scope?: string;
}
interface SchwabOAuthState {
  provider?: string;
  issued_at?: number;
  nonce?: string;
}

const TOKEN_KEY = "upstox/current-token";
const OAUTH_STATE_KEY_PREFIX = "upstox/oauth-state/";
const SCHWAB_TOKEN_KEY = "schwab/current-token";
const SCHWAB_OAUTH_STATE_KEY_PREFIX = "schwab/oauth-state/";
const UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token";
const UPSTOX_PROFILE_URL = "https://api.upstox.com/v2/user/profile";
const SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token";
const CALLBACK_URL = "https://factorlab-upstox-oauth-callback.kairo-jai.workers.dev/oauth/callback";
const SCHWAB_REFRESH_TOKEN_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const SCHWAB_OAUTH_STATE_TTL_MS = 10 * 60 * 1000;

function base64Encode(bytes: Uint8Array): string {
  let binary = ""; for (const byte of bytes) binary += String.fromCharCode(byte); return btoa(binary);
}
function base64Decode(value: string): ArrayBuffer {
  const binary = atob(value); return Uint8Array.from(binary, c => c.charCodeAt(0)).buffer as ArrayBuffer;
}
function base64UrlDecode(value: string): ArrayBuffer {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  return base64Decode(value.replaceAll("-", "+").replaceAll("_", "/") + padding);
}
function text(message: string, status = 200): Response { return new Response(message, { status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } }); }
function nextUpstoxExpiry(now = new Date()): Date {
  const offset = 330 * 60 * 1000; const ist = new Date(now.getTime() + offset);
  const tomorrow = ist.getUTCHours() > 3 || (ist.getUTCHours() === 3 && ist.getUTCMinutes() >= 30);
  return new Date(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate() + (tomorrow ? 1 : 0), 3, 30) - offset);
}
async function key(env: Env): Promise<CryptoKey> {
  const raw = base64Decode((await env.TOKEN_ENCRYPTION_KEY.get()).trim());
  if (raw.byteLength !== 32) throw new Error("Invalid token encryption key");
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}
async function encryptValue(env: Env, value: string): Promise<EncryptedValue> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await key(env),
    new TextEncoder().encode(value),
  );
  return { iv: base64Encode(iv), ciphertext: base64Encode(new Uint8Array(encrypted)) };
}
function basicAuthorization(appKey: string, appSecret: string): string {
  return `Basic ${base64Encode(new TextEncoder().encode(`${appKey}:${appSecret}`))}`;
}

async function validSealedSchwabState(env: Env, state: string): Promise<boolean> {
  try {
    const sealed = new Uint8Array(base64UrlDecode(state));
    if (sealed.byteLength <= 28) return false;
    const plaintext = await crypto.subtle.decrypt(
      { name: "AES-GCM", iv: sealed.slice(0, 12) },
      await key(env),
      sealed.slice(12),
    );
    const payload = JSON.parse(new TextDecoder().decode(plaintext)) as SchwabOAuthState;
    const age = Date.now() - Number(payload.issued_at);
    return payload.provider === "schwab" &&
      typeof payload.nonce === "string" && payload.nonce.length >= 32 &&
      Number.isFinite(age) && age >= -60_000 && age <= SCHWAB_OAUTH_STATE_TTL_MS;
  } catch {
    return false;
  }
}

async function consumeSchwabState(env: Env, state: string): Promise<boolean> {
  if (await validSealedSchwabState(env, state)) return true;

  // Accept the old KV-backed format during rolling deployment and for logins
  // that began just before this version was published.
  const legacyKey = `${SCHWAB_OAUTH_STATE_KEY_PREFIX}${state}`;
  if (!await env.RUNTIME_STATE.get(legacyKey)) return false;
  await env.RUNTIME_STATE.delete(legacyKey);
  return true;
}

function oauthErrorDetail(payload: unknown): string {
  if (!payload || typeof payload !== "object") return "";
  const value = payload as { error_description?: unknown; error?: unknown; message?: unknown };
  const detail = [value.error_description, value.error, value.message]
    .find((candidate): candidate is string => typeof candidate === "string" && candidate.trim().length > 0);
  return detail ? detail.replaceAll(/\s+/g, " ").trim().slice(0, 240) : "";
}
async function profile(token: string): Promise<UpstoxProfile> {
  const response = await fetch(UPSTOX_PROFILE_URL, { headers: { Accept: "application/json", Authorization: `Bearer ${token}` } });
  const payload = await response.json() as { status?: string; data?: UpstoxProfile };
  if (!response.ok || payload.status !== "success" || !payload.data?.user_id) throw new Error("Upstox profile validation failed");
  return payload.data;
}
async function callback(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url); const code = url.searchParams.get("code") ?? ""; const state = url.searchParams.get("state") ?? "";
  if (!code || !state || !await env.RUNTIME_STATE.get(`${OAUTH_STATE_KEY_PREFIX}${state}`)) return text("Invalid or expired OAuth state.", 400);
  await env.RUNTIME_STATE.delete(`${OAUTH_STATE_KEY_PREFIX}${state}`);
  const [apiKey, apiSecret] = await Promise.all([env.UPSTOX_API_KEY.get(), env.UPSTOX_API_SECRET.get()]);
  const form = new URLSearchParams({ code, client_id: apiKey, client_secret: apiSecret, redirect_uri: CALLBACK_URL, grant_type: "authorization_code" });
  const exchange = await fetch(UPSTOX_TOKEN_URL, { method: "POST", headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" }, body: form });
  const payload = await exchange.json() as { access_token?: string };
  if (!exchange.ok || !payload.access_token) return text(`Upstox token exchange failed (${exchange.status}).`, 502);
  const owner = await profile(payload.access_token); const iv = crypto.getRandomValues(new Uint8Array(12));
  const encrypted = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await key(env), new TextEncoder().encode(payload.access_token));
  const issued = new Date(); const record: EncryptedToken = { version: 1, iv: base64Encode(iv), ciphertext: base64Encode(new Uint8Array(encrypted)), issued_at: issued.toISOString(), expires_at: nextUpstoxExpiry(issued).toISOString(), user_id: owner.user_id ?? "", user_name: owner.user_name ?? "" };
  await env.RUNTIME_STATE.put(TOKEN_KEY, JSON.stringify(record));
  return Response.redirect(env.MANAGEMENT_URL, 303);
}

async function schwabCallback(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  const code = url.searchParams.get("code") ?? "";
  const state = url.searchParams.get("state") ?? "";
  if (!state || !await consumeSchwabState(env, state)) {
    return text("Invalid or expired OAuth state.", 400);
  }
  const authorizationError = url.searchParams.get("error_description") || url.searchParams.get("error");
  if (authorizationError) {
    return text(`Schwab authorization failed: ${authorizationError.replaceAll(/\s+/g, " ").trim().slice(0, 240)}`, 400);
  }
  if (!code) return text("Schwab authorization did not return a code.", 400);

  const [appKey, appSecret] = await Promise.all([
    env.SCHWAB_APP_KEY.get(),
    env.SCHWAB_APP_SECRET.get(),
  ]);
  const exchange = await fetch(SCHWAB_TOKEN_URL, {
    method: "POST",
    headers: {
      Accept: "application/json",
      Authorization: basicAuthorization(appKey, appSecret),
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      code,
      redirect_uri: env.SCHWAB_CALLBACK_URL,
    }),
  });
  const exchangeBody = await exchange.text();
  let payload: SchwabTokenPayload = {};
  try {
    payload = JSON.parse(exchangeBody) as SchwabTokenPayload;
  } catch {
    // The status below remains useful if Schwab returns a proxy/HTML error.
  }
  if (!exchange.ok || !payload.access_token || !payload.refresh_token) {
    const detail = oauthErrorDetail(payload);
    return text(`Schwab token exchange failed (${exchange.status})${detail ? `: ${detail}` : "."}`, 502);
  }

  const issuedAt = new Date();
  const expiresIn = Number.isFinite(payload.expires_in) ? Number(payload.expires_in) : 1800;
  const record: SchwabToken = {
    version: 1,
    access_token: await encryptValue(env, payload.access_token),
    refresh_token: await encryptValue(env, payload.refresh_token),
    issued_at: issuedAt.toISOString(),
    access_expires_at: new Date(issuedAt.getTime() + expiresIn * 1000).toISOString(),
    refresh_expires_at: new Date(issuedAt.getTime() + SCHWAB_REFRESH_TOKEN_TTL_MS).toISOString(),
    token_type: payload.token_type || "Bearer",
    scope: payload.scope || "",
  };
  await env.RUNTIME_STATE.put(SCHWAB_TOKEN_KEY, JSON.stringify(record));
  return Response.redirect(env.MANAGEMENT_URL, 303);
}
export default { async fetch(request: Request, env: Env): Promise<Response> {
  try { const path = new URL(request.url).pathname; if (path === "/health") return Response.json({ status: "ok" }); if (path === "/oauth/callback" && request.method === "GET") return callback(request, env); if (path === "/oauth/schwab/callback" && request.method === "GET") return schwabCallback(request, env); return text("Not found.", 404); }
  catch (error) { console.error(error); return text("OAuth callback failed.", 500); }
} } satisfies ExportedHandler<Env>;
