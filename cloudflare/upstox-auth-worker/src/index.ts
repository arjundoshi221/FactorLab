interface SecretBinding {
  get(): Promise<string>;
}

interface Env {
  RUNTIME_STATE: KVNamespace;
  UPSTOX_API_KEY: SecretBinding;
  UPSTOX_API_SECRET: SecretBinding;
  SCHWAB_APP_KEY: SecretBinding;
  SCHWAB_APP_SECRET: SecretBinding;
  TOKEN_ENCRYPTION_KEY: SecretBinding;
  CLICKHOUSE_PASSWORD: SecretBinding;
  CLICKHOUSE_PASSWORD_SHA256: SecretBinding;
  EODHD_API_KEY: SecretBinding;
  FACTORLAB_API_KEY: SecretBinding;
  // Optional IB Gateway logins; absent bindings or secrets render as null.
  IBKR_PAPER_PASSWORD?: SecretBinding;
  IBKR_LIVE_PASSWORD?: SecretBinding;
  IBKR_VNC_PASSWORD?: SecretBinding;
  UPSTOX_REDIRECT_URL: string;
  SCHWAB_CALLBACK_URL: string;
  // Comma-separated pooled Schwab accounts; `primary` is always included.
  SCHWAB_ACCOUNTS?: string;
  CF_ACCESS_TEAM_DOMAIN: string;
  CF_ACCESS_AUD: string;
}

interface EncryptedToken {
  version: 1;
  iv: string;
  ciphertext: string;
  issued_at: string;
  expires_at: string;
  user_id: string;
  user_name: string;
}

interface UpstoxProfile {
  user_id?: string;
  user_name?: string;
  email?: string;
}

interface EncryptedValue {
  iv: string;
  ciphertext: string;
}

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
  provider: "schwab";
  issued_at: number;
  nonce: string;
  account: string;
}

const TOKEN_KEY = "upstox/current-token";
const OAUTH_STATE_KEY_PREFIX = "upstox/oauth-state/";
const SCHWAB_TOKEN_KEY = "schwab/current-token";
const UPSTOX_DIALOG_URL = "https://api.upstox.com/v2/login/authorization/dialog";
const UPSTOX_TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token";
const UPSTOX_PROFILE_URL = "https://api.upstox.com/v2/user/profile";
const SCHWAB_AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize";
const SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token";
// Refresh well before expiry: the VPS secrets agent polls every 60s, so a 60s window
// let collectors hold an expired token between polls.
const SCHWAB_REFRESH_WINDOW_MS = 5 * 60_000;
// Pooled Schwab accounts (one developer app each). `primary` keeps the legacy names;
// any other id uses SCHWAB_APP_KEY_<ID> / SCHWAB_APP_SECRET_<ID> (optional Secrets Store
// bindings), KV schwab/accounts/<id>/current-token and SCHWAB_ACCESS_TOKEN_<ID>.
const SCHWAB_PRIMARY_ACCOUNT = "primary";
const SCHWAB_ACCOUNT_ID = /^[a-z0-9_]{1,32}$/;

function base64Encode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

function base64UrlEncode(bytes: Uint8Array): string {
  return base64Encode(bytes)
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function base64Decode(value: string): ArrayBuffer {
  const binary = atob(value);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0)).buffer as ArrayBuffer;
}

function base64UrlDecode(value: string): ArrayBuffer {
  const padding = "=".repeat((4 - (value.length % 4)) % 4);
  return base64Decode(value.replaceAll("-", "+").replaceAll("_", "/") + padding);
}

function decodeJson<T>(value: string): T {
  return JSON.parse(new TextDecoder().decode(base64UrlDecode(value))) as T;
}

function randomToken(): string {
  return base64Encode(crypto.getRandomValues(new Uint8Array(32)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
}

function nextUpstoxExpiry(now = new Date()): Date {
  const istOffsetMs = 330 * 60 * 1000;
  const ist = new Date(now.getTime() + istOffsetMs);
  const afterCutoff = ist.getUTCHours() > 3 ||
    (ist.getUTCHours() === 3 && ist.getUTCMinutes() >= 30);
  const expiryAsUtc = Date.UTC(
    ist.getUTCFullYear(),
    ist.getUTCMonth(),
    ist.getUTCDate() + (afterCutoff ? 1 : 0),
    3,
    30,
  );
  return new Date(expiryAsUtc - istOffsetMs);
}

async function encryptionKey(env: Env): Promise<CryptoKey> {
  const encoded = await env.TOKEN_ENCRYPTION_KEY.get();
  const raw = base64Decode(encoded.trim());
  if (raw.byteLength !== 32) {
    throw new Error("TOKEN_ENCRYPTION_KEY must be a base64-encoded 32-byte value");
  }
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

async function encryptValue(env: Env, value: string): Promise<EncryptedValue> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await encryptionKey(env),
    new TextEncoder().encode(value),
  );
  return {
    iv: base64Encode(iv),
    ciphertext: base64Encode(new Uint8Array(ciphertext)),
  };
}

async function decryptValue(env: Env, value: EncryptedValue): Promise<string> {
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: new Uint8Array(base64Decode(value.iv)) },
    await encryptionKey(env),
    base64Decode(value.ciphertext),
  );
  return new TextDecoder().decode(plaintext);
}

async function encryptAccessToken(
  env: Env,
  token: string,
  profile: UpstoxProfile,
): Promise<EncryptedToken> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await encryptionKey(env),
    new TextEncoder().encode(token),
  );
  const issuedAt = new Date();
  return {
    version: 1,
    iv: base64Encode(iv),
    ciphertext: base64Encode(new Uint8Array(ciphertext)),
    issued_at: issuedAt.toISOString(),
    expires_at: nextUpstoxExpiry(issuedAt).toISOString(),
    user_id: profile.user_id ?? "",
    user_name: profile.user_name ?? "",
  };
}

async function decryptAccessToken(env: Env, record: EncryptedToken): Promise<string> {
  const plaintext = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: new Uint8Array(base64Decode(record.iv)) },
    await encryptionKey(env),
    base64Decode(record.ciphertext),
  );
  return new TextDecoder().decode(plaintext);
}

async function tokenRecord(env: Env): Promise<EncryptedToken | null> {
  return env.RUNTIME_STATE.get<EncryptedToken>(TOKEN_KEY, "json");
}

async function validateUpstoxToken(token: string): Promise<UpstoxProfile> {
  const response = await fetch(UPSTOX_PROFILE_URL, {
    headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`Upstox token validation failed (${response.status})`);
  const payload = await response.json() as { status?: string; data?: UpstoxProfile };
  if (payload.status !== "success" || !payload.data?.user_id) {
    throw new Error("Upstox returned an invalid profile response");
  }
  return payload.data;
}

async function beginLogin(request: Request, env: Env): Promise<Response> {
  const state = randomToken();
  await env.RUNTIME_STATE.put(`${OAUTH_STATE_KEY_PREFIX}${state}`, "1", { expirationTtl: 600 });
  const apiKey = await env.UPSTOX_API_KEY.get();
  const target = new URL(UPSTOX_DIALOG_URL);
  target.searchParams.set("response_type", "code");
  target.searchParams.set("client_id", apiKey);
  target.searchParams.set("redirect_uri", env.UPSTOX_REDIRECT_URL);
  target.searchParams.set("state", state);

  return new Response(null, {
    status: 302,
    headers: {
      Location: target.toString(),
    },
  });
}

async function beginSchwabLogin(request: Request, env: Env): Promise<Response> {
  const account = new URL(request.url).searchParams.get("account") || SCHWAB_PRIMARY_ACCOUNT;
  if (!schwabAccounts(env).includes(account)) return textResponse("Unknown Schwab account.", 400);
  const credentials = await schwabCredentials(env, account);
  if (!credentials) return textResponse(`Schwab account ${account} has no app credentials configured.`, 400);
  const state = await createSchwabOAuthState(env, account);
  const [appKey] = credentials;
  const target = new URL(SCHWAB_AUTHORIZE_URL);
  target.searchParams.set("response_type", "code");
  target.searchParams.set("client_id", appKey);
  target.searchParams.set("redirect_uri", env.SCHWAB_CALLBACK_URL);
  target.searchParams.set("state", state);
  return new Response(null, { status: 302, headers: { Location: target.toString() } });
}

async function createSchwabOAuthState(env: Env, account: string): Promise<string> {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const payload: SchwabOAuthState = {
    provider: "schwab",
    issued_at: Date.now(),
    nonce: randomToken(),
    account,
  };
  const ciphertext = await crypto.subtle.encrypt(
    { name: "AES-GCM", iv },
    await encryptionKey(env),
    new TextEncoder().encode(JSON.stringify(payload)),
  );
  const sealed = new Uint8Array(iv.byteLength + ciphertext.byteLength);
  sealed.set(iv);
  sealed.set(new Uint8Array(ciphertext), iv.byteLength);
  return base64UrlEncode(sealed);
}

function schwabBasicAuthorization(appKey: string, appSecret: string): string {
  return `Basic ${base64Encode(new TextEncoder().encode(`${appKey}:${appSecret}`))}`;
}

function schwabAccounts(env: Env): string[] {
  const listed = (env.SCHWAB_ACCOUNTS ?? "").split(",").map((id) => id.trim());
  return [...new Set([SCHWAB_PRIMARY_ACCOUNT, ...listed.filter((id) => SCHWAB_ACCOUNT_ID.test(id))])];
}

function schwabTokenKey(account: string): string {
  return account === SCHWAB_PRIMARY_ACCOUNT ? SCHWAB_TOKEN_KEY : `schwab/accounts/${account}/current-token`;
}

function schwabSuffixed(base: string, account: string): string {
  return account === SCHWAB_PRIMARY_ACCOUNT ? base : `${base}_${account.toUpperCase()}`;
}

async function schwabCredentials(env: Env, account: string): Promise<[string, string] | null> {
  const bindings = env as unknown as Record<string, SecretBinding | undefined>;
  const [appKey, appSecret] = await Promise.all([
    optionalSecret(bindings[schwabSuffixed("SCHWAB_APP_KEY", account)]),
    optionalSecret(bindings[schwabSuffixed("SCHWAB_APP_SECRET", account)]),
  ]);
  return appKey && appSecret ? [appKey, appSecret] : null;
}

async function schwabTokenRecord(env: Env, account: string): Promise<SchwabToken | null> {
  return env.RUNTIME_STATE.get<SchwabToken>(schwabTokenKey(account), "json");
}

async function refreshSchwabToken(env: Env, account: string, record: SchwabToken): Promise<SchwabToken> {
  const [credentials, refreshToken] = await Promise.all([
    schwabCredentials(env, account),
    decryptValue(env, record.refresh_token),
  ]);
  if (!credentials) throw new Error(`Schwab account ${account} has no app credentials`);
  const [appKey, appSecret] = credentials;
  const response = await fetch(SCHWAB_TOKEN_URL, {
    method: "POST",
    headers: {
      Accept: "application/json",
      Authorization: schwabBasicAuthorization(appKey, appSecret),
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams({ grant_type: "refresh_token", refresh_token: refreshToken }),
  });
  const payload = await response.json() as SchwabTokenPayload;
  if (!response.ok || !payload.access_token) {
    throw new Error(`Schwab token refresh failed (${response.status})`);
  }

  const issuedAt = new Date();
  const expiresIn = Number.isFinite(payload.expires_in) ? Number(payload.expires_in) : 1800;
  const nextRefreshToken = payload.refresh_token || refreshToken;
  const updated: SchwabToken = {
    version: 1,
    access_token: await encryptValue(env, payload.access_token),
    refresh_token: await encryptValue(env, nextRefreshToken),
    issued_at: issuedAt.toISOString(),
    access_expires_at: new Date(issuedAt.getTime() + expiresIn * 1000).toISOString(),
    refresh_expires_at: record.refresh_expires_at,
    token_type: payload.token_type || record.token_type || "Bearer",
    scope: payload.scope || record.scope,
  };
  await env.RUNTIME_STATE.put(schwabTokenKey(account), JSON.stringify(updated));
  return updated;
}

interface SchwabRuntimeToken {
  token: string | null;
  status: "missing" | "unconfigured" | "valid" | "reauth_required" | "refresh_failed";
  record: SchwabToken | null;
}

async function resolveSchwabRuntimeToken(env: Env, account: string): Promise<SchwabRuntimeToken> {
  let record = await schwabTokenRecord(env, account);
  if (!record) {
    const configured = await schwabCredentials(env, account) !== null;
    return { token: null, status: configured ? "missing" : "unconfigured", record: null };
  }
  if (Date.parse(record.refresh_expires_at) <= Date.now()) {
    return { token: null, status: "reauth_required", record };
  }
  if (Date.parse(record.access_expires_at) <= Date.now() + SCHWAB_REFRESH_WINDOW_MS) {
    try {
      record = await refreshSchwabToken(env, account, record);
    } catch (error) {
      console.error(`Schwab ${account} token refresh failed`, error instanceof Error ? error.message : "unknown error");
      return { token: null, status: "refresh_failed", record };
    }
  }
  return { token: await decryptValue(env, record.access_token), status: "valid", record };
}

function schwabRuntimeSummary(resolved: SchwabRuntimeToken): Record<string, string> {
  return resolved.record ? {
    status: resolved.status,
    issued_at: resolved.record.issued_at,
    access_expires_at: resolved.record.access_expires_at,
    refresh_expires_at: resolved.record.refresh_expires_at,
  } : { status: resolved.status };
}

function schwabRecordStatus(record: SchwabToken | null): Record<string, string> {
  if (!record) return { status: "missing" };
  const status = Date.parse(record.refresh_expires_at) <= Date.now()
    ? "reauth_required"
    : Date.parse(record.access_expires_at) <= Date.now()
      ? "refresh_pending"
      : "valid";
  return {
    status,
    issued_at: record.issued_at,
    access_expires_at: record.access_expires_at,
    refresh_expires_at: record.refresh_expires_at,
  };
}

interface AccessJwtHeader {
  alg?: string;
  kid?: string;
}

interface AccessJwtClaims {
  aud?: string[] | string;
  exp?: number;
}

interface AccessJwk {
  alg?: string;
  e?: string;
  kid?: string;
  kty?: string;
  n?: string;
  use?: string;
}

async function verifiedAccess(request: Request, env: Env): Promise<boolean> {
  const token = request.headers.get("Cf-Access-Jwt-Assertion");
  if (!token || !env.CF_ACCESS_TEAM_DOMAIN || !env.CF_ACCESS_AUD) return false;

  const [encodedHeader, encodedClaims, encodedSignature, ...extra] = token.split(".");
  if (!encodedHeader || !encodedClaims || !encodedSignature || extra.length) return false;

  try {
    const header = decodeJson<AccessJwtHeader>(encodedHeader);
    const claims = decodeJson<AccessJwtClaims>(encodedClaims);
    const audience = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
    if (
      header.alg !== "RS256" ||
      !header.kid ||
      !audience.includes(env.CF_ACCESS_AUD) ||
      !claims.exp ||
      claims.exp <= Math.floor(Date.now() / 1000)
    ) return false;

    const certsUrl = `https://${env.CF_ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`;
    const response = await fetch(certsUrl, { cf: { cacheTtl: 3600 } });
    if (!response.ok) return false;
    const body = await response.json() as { keys?: AccessJwk[] };
    const jwk = body.keys?.find((key) => key.kid === header.kid && key.kty === "RSA");
    if (!jwk) return false;
    const key = await crypto.subtle.importKey(
      "jwk",
      jwk,
      { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
      false,
      ["verify"],
    );
    return crypto.subtle.verify(
      "RSASSA-PKCS1-v1_5",
      key,
      base64UrlDecode(encodedSignature),
      new TextEncoder().encode(`${encodedHeader}.${encodedClaims}`),
    );
  } catch {
    return false;
  }
}

async function optionalSecret(binding: SecretBinding | undefined): Promise<string | null> {
  if (!binding) return null;
  try {
    const value = await binding.get();
    return value.trim() ? value : null;
  } catch {
    return null;
  }
}

async function runtimeSecrets(request: Request, env: Env): Promise<Response> {
  if (request.method !== "POST") return textResponse("Method not allowed.", 405);
  if (!await verifiedAccess(request, env)) return textResponse("Cloudflare Access required.", 403);

  const record = await tokenRecord(env);
  const valid = record !== null && Date.parse(record.expires_at) > Date.now();
  const accounts = schwabAccounts(env);
  const [
    clickhousePassword, clickhouseHash, eodhdKey, factorlabApiKey, schwabPool,
    ibkrPaperPassword, ibkrLivePassword, ibkrVncPassword,
  ] = await Promise.all([
    env.CLICKHOUSE_PASSWORD.get(),
    env.CLICKHOUSE_PASSWORD_SHA256.get(),
    env.EODHD_API_KEY.get(),
    env.FACTORLAB_API_KEY.get(),
    Promise.all(accounts.map((account) => resolveSchwabRuntimeToken(env, account))),
    optionalSecret(env.IBKR_PAPER_PASSWORD),
    optionalSecret(env.IBKR_LIVE_PASSWORD),
    optionalSecret(env.IBKR_VNC_PASSWORD),
  ]);
  const accessToken = valid && record ? await decryptAccessToken(env, record) : null;
  const schwab = schwabPool[0];  // primary keeps the legacy fields below
  const pooledTokens = Object.fromEntries(accounts.slice(1).map(
    (account, index) => [schwabSuffixed("SCHWAB_ACCESS_TOKEN", account), schwabPool[index + 1].token],
  ));
  return jsonResponse({
    version: 1,
    generated_at: new Date().toISOString(),
    secrets: {
      CLICKHOUSE_PASSWORD: clickhousePassword,
      CLICKHOUSE_PASSWORD_SHA256: clickhouseHash,
      UPSTOX_ACCESS_TOKEN: accessToken,
      SCHWAB_ACCESS_TOKEN: schwab.token,
      EODHD_API_KEY: eodhdKey,
      FACTORLAB_API_KEY: factorlabApiKey,
      IBKR_PAPER_PASSWORD: ibkrPaperPassword,
      IBKR_LIVE_PASSWORD: ibkrLivePassword,
      IBKR_VNC_PASSWORD: ibkrVncPassword,
      ...pooledTokens,
    },
    upstox: record ? {
      status: valid ? "valid" : "expired",
      issued_at: record.issued_at,
      expires_at: record.expires_at,
      user_id: record.user_id,
    } : { status: "missing" },
    schwab: schwab.record ? {
      status: schwab.status,
      issued_at: schwab.record.issued_at,
      access_expires_at: schwab.record.access_expires_at,
      refresh_expires_at: schwab.record.refresh_expires_at,
    } : { status: "missing" },
    schwab_accounts: Object.fromEntries(
      accounts.map((account, index) => [account, schwabRuntimeSummary(schwabPool[index])]),
    ),
  });
}

async function statusResponse(env: Env): Promise<Response> {
  const accounts = schwabAccounts(env);
  const [upstox, schwabRecords] = await Promise.all([
    tokenRecord(env),
    Promise.all(accounts.map((account) => schwabTokenRecord(env, account))),
  ]);
  return jsonResponse({
    status: upstox
      ? (Date.parse(upstox.expires_at) > Date.now() ? "valid" : "expired")
      : "missing",
    issued_at: upstox?.issued_at,
    expires_at: upstox?.expires_at,
    user_name: upstox?.user_name,
    upstox: upstox ? {
      status: Date.parse(upstox.expires_at) > Date.now() ? "valid" : "expired",
      issued_at: upstox.issued_at,
      expires_at: upstox.expires_at,
      user_name: upstox.user_name,
    } : { status: "missing" },
    schwab: schwabRecordStatus(schwabRecords[0]),
    schwab_accounts: Object.fromEntries(
      accounts.map((account, index) => [account, schwabRecordStatus(schwabRecords[index])]),
    ),
  });
}

function managementPage(): Response {
  return new Response(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>FactorLab Broker Auth</title><style>
body{font:16px system-ui;max-width:680px;margin:4rem auto;padding:0 1rem;color:#18212f}
.card{border:1px solid #d7dde5;border-radius:12px;padding:1.5rem;margin:1rem 0}button{background:#172554;color:white;
border:0;border-radius:8px;padding:.8rem 1.1rem;font-weight:600;cursor:pointer}dt{font-weight:600;margin-top:.8rem}
</style></head><body><h1>FactorLab broker authentication</h1><div class="card"><h2>Upstox</h2>
<dl><dt>Status</dt><dd id="upstox-status">Loading…</dd><dt>User</dt><dd id="upstox-user">—</dd>
<dt>Issued</dt><dd id="upstox-issued">—</dd><dt>Expires</dt><dd id="upstox-expires">—</dd></dl>
<button onclick="location.href='/api/upstox/login'">Refresh Upstox token</button></div>
<div id="schwab-accounts"><div class="card"><h2>Charles Schwab</h2><p id="schwab-status">Loading…</p></div></div>
<script>function schwabCard(id,a){const card=document.createElement('div');card.className='card';
const h=document.createElement('h2');h.textContent='Charles Schwab: '+id;const dl=document.createElement('dl');
for(const [k,v] of [['Status',a.status],['Access token expires',a.access_expires_at],['Reauthentication due',a.refresh_expires_at]]){
const dt=document.createElement('dt');dt.textContent=k;const dd=document.createElement('dd');dd.textContent=String(v||'—');dl.append(dt,dd)}
const b=document.createElement('button');b.textContent='Authenticate '+id;
b.onclick=()=>{location.href='/api/schwab/login?account='+encodeURIComponent(id)};card.append(h,dl,b);return card}
fetch('/api/status',{credentials:'same-origin'}).then(r=>r.json()).then(s=>{
document.querySelector('#upstox-status').textContent=String(s.upstox.status);
document.querySelector('#upstox-user').textContent=String(s.upstox.user_name||'—');
document.querySelector('#upstox-issued').textContent=String(s.upstox.issued_at||'—');
document.querySelector('#upstox-expires').textContent=String(s.upstox.expires_at||'—');
document.querySelector('#schwab-accounts').replaceChildren(
...Object.entries(s.schwab_accounts||{primary:s.schwab}).map(([id,a])=>schwabCard(id,a)))}).catch(()=>{
document.querySelector('#upstox-status').textContent='Unable to load status.';
document.querySelector('#schwab-status').textContent='Unable to load status.'})</script></body></html>`, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

function textResponse(message: string, status = 200): Response {
  return new Response(message, { status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
}

function jsonResponse(value: unknown, status = 200): Response {
  return Response.json(value, { status });
}

function withSecurityHeaders(response: Response): Response {
  const secured = new Response(response.body, response);
  secured.headers.set("Cache-Control", "no-store, no-cache, must-revalidate");
  secured.headers.set("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'");
  secured.headers.set("Referrer-Policy", "no-referrer");
  secured.headers.set("X-Content-Type-Options", "nosniff");
  secured.headers.set("X-Frame-Options", "DENY");
  return secured;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      const path = new URL(request.url).pathname;
      let response: Response;
      if (path !== "/health" && path !== "/v1/runtime-secrets" && !await verifiedAccess(request, env)) {
        response = textResponse("Cloudflare Access required.", 403);
      } else if (path === "/" && request.method === "GET") response = managementPage();
      else if ((path === "/api/login" || path === "/api/upstox/login") && request.method === "GET") response = await beginLogin(request, env);
      else if (path === "/api/schwab/login" && request.method === "GET") response = await beginSchwabLogin(request, env);
      else if (path === "/api/status" && request.method === "GET") response = await statusResponse(env);
      else if (path === "/v1/runtime-secrets") response = await runtimeSecrets(request, env);
      else if (path === "/health" && request.method === "GET") response = jsonResponse({ status: "ok" });
      else response = textResponse("Not found.", 404);
      return withSecurityHeaders(response);
    } catch (error) {
      console.error("Request failed", error instanceof Error ? error.message : "unknown error");
      return withSecurityHeaders(textResponse("Internal error.", 500));
    }
  },
} satisfies ExportedHandler<Env>;
