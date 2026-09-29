import { useCallback, useEffect, useMemo, useState } from "react";

interface Container {
  name: string; status: string; started_at: string | null;
  service?: string | null; image_ref?: string | null;
}
interface Labels { revision?: string | null; version?: string | null; source?: string | null }
interface Image {
  id: string; tags: string[]; digests: string[]; size_bytes: number; created_at: string;
  containers: Container[]; last_container_start_at: string | null; release_activated_at: string | null;
  current_release?: boolean; platform?: string | null; labels?: Labels;
}
interface Release {
  id: string; commit: string | null; image: string | null;
  previous_release: string | null; previous_image: string | null; activated_at: string | null;
}
interface Build { release_id: string | null; commit: string | null }
interface Snapshot {
  snapshot_at: string; release_id: string | null; stale: boolean; images: Image[];
  release?: Release | null; releases?: Release[]; api_build?: Build;
}
type Sort = "name" | "size" | "created" | "started" | "activated";
type UseFilter = "all" | "in_use" | "unused";

const REPOSITORY = "https://github.com/arjundoshi221/FactorLab";

function date(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "—" : new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata",
  }).format(parsed);
}

function age(value: string | null | undefined, now: number): string {
  if (!value) return "";
  const parsed = new Date(value).valueOf();
  if (Number.isNaN(parsed)) return "";
  const minutes = Math.max(Math.round((now - parsed) / 60_000), 0);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

function size(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

/** Shorten sha256 digests and image IDs while keeping the repository readable. */
function short(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/sha256:([0-9a-f]{12})[0-9a-f]+/g, "sha256:$1");
}

function isRunning(image: Image): boolean {
  return image.containers.some((container) => container.status === "running");
}

function imageName(image: Image): string {
  return image.tags[0] ?? image.digests[0]?.split("@")[0] ?? "Untagged";
}

function Commit({ value }: { value: string | null | undefined }) {
  if (!value) return <>—</>;
  return <a href={`${REPOSITORY}/commit/${value}`} target="_blank" rel="noreferrer"><code>{value.slice(0, 12)}</code></a>;
}

function ReleaseCard({ snapshot }: { snapshot: Snapshot }) {
  const release = snapshot.release;
  const build = snapshot.api_build;
  const releaseId = release?.id ?? snapshot.release_id;
  const mismatch = Boolean(build?.release_id && releaseId && build.release_id !== releaseId);
  return <section className="release-card" aria-labelledby="release-title">
    <div className="release-card__heading">
      <div>
        <span className="eyebrow">Running release</span>
        <h2 id="release-title">{releaseId ?? "No release recorded"}</h2>
        <p>{release?.activated_at ? `Activated ${date(release.activated_at)}` : "No activation time is recorded for this release."}</p>
      </div>
      <span className={`status status--${mismatch ? "attention" : releaseId ? "healthy" : "unknown"}`}>
        <span className="status__dot" aria-hidden="true" />
        {mismatch ? "API build differs" : releaseId ? "Verified release" : "Unknown"}
      </span>
    </div>
    <dl className="release-facts">
      <div><dt>Commit</dt><dd><Commit value={release?.commit ?? build?.commit} /></dd></div>
      <div><dt>Image</dt><dd title={release?.image ?? undefined}><code>{short(release?.image)}</code></dd></div>
      <div><dt>API build</dt><dd>{build?.release_id ?? "Not baked into this image"}</dd></div>
      <div><dt>Previous release</dt><dd>{release?.previous_release && release.previous_release !== "unknown" ? release.previous_release : "—"}</dd></div>
    </dl>
    {mismatch && <p className="notice" role="status">The API container reports release {build?.release_id}, but the host records {releaseId}. Check the latest release workflow.</p>}
  </section>;
}

export function DockerImages() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [use, setUse] = useState<UseFilter>("all");
  const [sort, setSort] = useState<Sort>("name");
  const [descending, setDescending] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch("/hub/api/v1/docker-images", { cache: "no-store" });
      if (!response.ok) throw new Error("Snapshot unavailable");
      setSnapshot(await response.json() as Snapshot);
      setError(false);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => { if (!document.hidden) void refresh(); }, 60_000);
    return () => window.clearInterval(interval);
  }, [refresh]);

  const now = snapshot ? new Date(snapshot.snapshot_at).valueOf() : Date.now();
  const all = useMemo(() => snapshot?.images ?? [], [snapshot]);
  const totals = useMemo(() => {
    const running = all.filter(isRunning);
    const unused = all.filter((image) => !isRunning(image));
    return {
      running: running.length,
      containers: all.reduce((count, image) => count + image.containers.filter((item) => item.status === "running").length, 0),
      untagged: all.filter((image) => image.tags.length === 0).length,
      bytes: all.reduce((sum, image) => sum + image.size_bytes, 0),
      unused: unused.length,
      reclaimable: unused.reduce((sum, image) => sum + image.size_bytes, 0),
    };
  }, [all]);

  const services = useMemo(() => all
    .flatMap((image) => image.containers.map((container) => ({ image, container })))
    .sort((a, b) => (a.container.service ?? a.container.name).localeCompare(b.container.service ?? b.container.name)), [all]);

  const images = useMemo(() => {
    const query = search.trim().toLowerCase();
    const matching = all.filter((image) =>
      (use === "all" || (use === "in_use") === isRunning(image)) &&
      [image.id, ...image.tags, ...image.digests, image.labels?.revision ?? "",
        ...image.containers.flatMap((item) => [item.name, item.service ?? ""])]
        .some((value) => value.toLowerCase().includes(query)));
    const value = (image: Image): string | number => {
      switch (sort) {
        case "size": return image.size_bytes;
        case "created": return image.created_at;
        case "started": return image.last_container_start_at ?? "";
        case "activated": return image.release_activated_at ?? "";
        default: return image.tags[0] ?? image.id;
      }
    };
    return matching.sort((a, b) => {
      const left = value(a); const right = value(b);
      const order = typeof left === "number" && typeof right === "number"
        ? left - right : String(left).localeCompare(String(right));
      return (descending ? -order : order) || a.id.localeCompare(b.id);
    });
  }, [all, search, use, sort, descending]);

  function changeSort(next: Sort) {
    setDescending(sort === next ? !descending : next !== "name");
    setSort(next);
  }

  const releases = snapshot?.releases ?? [];
  const currentRelease = snapshot?.release?.id ?? snapshot?.release_id;

  return <main className="docker-page">
    <section className="hero">
      <div><span className="eyebrow">Production host</span><h1>Docker images</h1>
        <p>The release running on the VPS, which image each service uses, and every image stored on the host, including unused and untagged layers.</p></div>
      <div className="refresh-block">
        <span>{snapshot ? `Snapshot ${date(snapshot.snapshot_at)} · refreshes every minute` : "Reading the host inventory…"}</span>
        <button onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh images"}</button>
      </div>
    </section>

    {snapshot?.stale && <div className="notice" role="status">Snapshot is stale. The host collector has not updated it for over three minutes; check <code>factorlab-docker-images.timer</code>.</div>}
    {error && <div className="notice notice--error" role="alert">Docker image inventory is unavailable. Try refreshing.</div>}

    {snapshot && <>
      <ReleaseCard snapshot={snapshot} />

      <section className="metric-grid" aria-label="Image totals">
        <article className="metric-card"><span>Images stored</span><strong>{all.length}</strong><small>{totals.untagged} untagged</small></article>
        <article className="metric-card"><span>Images running</span><strong>{totals.running}</strong><small>{totals.containers} running containers</small></article>
        <article className="metric-card"><span>Disk used</span><strong>{size(totals.bytes)}</strong><small>Shared layers are counted per image</small></article>
        <article className="metric-card"><span>Reclaimable</span><strong>{size(totals.reclaimable)}</strong><small>{totals.unused} images without a running container</small></article>
      </section>

      <section className="inventory" aria-labelledby="services-title">
        <div className="section-heading"><div><span className="eyebrow">Compose services</span><h2 id="services-title">What each service runs</h2></div>
          <span>{services.length} containers</span></div>
        <div className="table-wrap"><table className="docker-services-table"><thead><tr>
          <th>Service</th><th>State</th><th>Image</th><th>Started</th><th>Release</th>
        </tr></thead><tbody>{services.map(({ image, container }) => <tr key={container.name}>
          <td data-label="Service"><strong>{container.service ?? container.name}</strong>{container.service && <small>{container.name}</small>}</td>
          <td data-label="State"><span className={`status status--${container.status === "running" ? "healthy" : container.status === "exited" ? "not_expected" : "attention"}`}>
            <span className="status__dot" aria-hidden="true" />{container.status}</span></td>
          <td data-label="Image" title={container.image_ref ?? image.id}><code>{short(container.image_ref ?? imageName(image))}</code></td>
          <td data-label="Started">{date(container.started_at)}<small>{age(container.started_at, now)}</small></td>
          <td data-label="Release">{image.current_release ? <span className="docker-badge docker-badge--current">Current</span> : <span className="docker-badge">Other image</span>}</td>
        </tr>)}</tbody></table>
          {services.length === 0 && <p className="no-results">No containers are recorded on this host.</p>}</div>
      </section>

      <section className="inventory" aria-labelledby="images-title">
        <div className="section-heading"><div><span className="eyebrow">Read-only inventory</span><h2 id="images-title">Stored images</h2></div>
          <span>{images.length} of {all.length} images</span></div>
        <div className="table-tools">
          <label><span className="sr-only">Search images</span>
            <input placeholder="Search images, services, or commits" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <div className="segmented" role="group" aria-label="Filter by use">
            {([["all", "All"], ["in_use", "Running"], ["unused", "Not running"]] as const).map(([key, label]) =>
              <button key={key} type="button" aria-pressed={use === key} className={use === key ? "is-active" : ""} onClick={() => setUse(key)}>{label}</button>)}
          </div>
        </div>
        <div className="table-wrap"><table><thead><tr>
          <th><button onClick={() => changeSort("name")}>Image</button></th>
          <th>Use</th><th className="numeric"><button onClick={() => changeSort("size")}>Size</button></th>
          <th><button onClick={() => changeSort("created")}>Created</button></th>
          <th>Revision</th>
          <th><button onClick={() => changeSort("started")}>Last container start</button></th>
          <th><button onClick={() => changeSort("activated")}>Release activated</button></th>
        </tr></thead><tbody>{images.map((image) => <tr key={image.id} className={image.current_release ? "is-current" : ""}>
          <td data-label="Image"><strong>{image.tags.length ? image.tags.join(", ") : "Untagged"}</strong>
            {image.current_release && <span className="docker-badge docker-badge--current">Current release</span>}
            <small title={image.id}><code>{short(image.id)}</code>{image.platform && ` · ${image.platform}`}</small>
            {image.digests.map((digest) => <small key={digest} title={digest}><code>{short(digest)}</code></small>)}</td>
          <td data-label="Use">{isRunning(image) ? "In use" : "Unused"}
            {image.containers.map((container) => <small key={container.name}>{container.service ?? container.name} ({container.status})</small>)}</td>
          <td data-label="Size" className="numeric">{size(image.size_bytes)}</td>
          <td data-label="Created">{date(image.created_at)}<small>{age(image.created_at, now)}</small></td>
          <td data-label="Revision">{image.labels?.revision ? <Commit value={image.labels.revision} /> : "—"}
            {image.labels?.version && <small>{image.labels.version}</small>}</td>
          <td data-label="Last container start">{date(image.last_container_start_at)}</td>
          <td data-label="Release activated">{date(image.release_activated_at)}</td>
        </tr>)}</tbody></table>
          {!loading && images.length === 0 && <p className="no-results">{all.length ? "No images match your filters." : "No Docker images are stored on this host."}</p>}</div>
      </section>

      {releases.length > 0 && <section className="inventory" aria-labelledby="history-title">
        <div className="section-heading"><div><span className="eyebrow">Deployment record</span><h2 id="history-title">Release history</h2></div>
          <span>Latest {releases.length} releases on this host</span></div>
        <ol className="release-history">{releases.map((release) => <li key={release.id} className={release.id === currentRelease ? "is-current" : ""}>
          <div><strong>{release.id}</strong><small>Commit <Commit value={release.commit} /></small></div>
          <div>{release.id === currentRelease && <span className="docker-badge docker-badge--current">Running</span>}
            <small>{release.activated_at ? `Activated ${date(release.activated_at)}` : "Not activated"}</small></div>
        </li>)}</ol>
      </section>}
    </>}
    <footer>FactorLab · Host snapshot via systemd collector · No Docker socket is exposed to the API</footer>
  </main>;
}
