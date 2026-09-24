import { useCallback, useEffect, useMemo, useState } from "react";

interface Container { name: string; status: string; started_at: string | null }
interface Image {
  id: string; tags: string[]; digests: string[]; size_bytes: number; created_at: string;
  containers: Container[]; last_container_start_at: string | null; release_activated_at: string | null;
}
interface Snapshot { snapshot_at: string; release_id: string | null; stale: boolean; images: Image[] }
type Sort = "name" | "size" | "created" | "started" | "activated";

function date(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "—" : new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata",
  }).format(parsed);
}

function size(bytes: number): string {
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

export function DockerImages() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
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
  useEffect(() => { void refresh(); }, [refresh]);

  const images = useMemo(() => {
    const query = search.trim().toLowerCase();
    const matching = (snapshot?.images ?? []).filter((image) =>
      [image.id, ...image.tags, ...image.digests, ...image.containers.map((item) => item.name)]
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
  }, [snapshot, search, sort, descending]);

  function changeSort(next: Sort) {
    setDescending(sort === next ? !descending : false);
    setSort(next);
  }

  return <main>
    <section className="hero"><div><span className="eyebrow">Production host</span><h1>Docker images</h1>
      <p>Images stored on the VPS, including unused and untagged images.</p></div></section>
    <section className="inventory">
      <div className="section-heading"><div><span className="eyebrow">Read-only inventory</span><h2>Stored images</h2></div>
        <button onClick={() => void refresh()} disabled={loading}>{loading ? "Refreshing…" : "Refresh images"}</button></div>
      {snapshot && <p>Snapshot: {date(snapshot.snapshot_at)} · {snapshot.images.length} images
        {snapshot.release_id && ` · Current release: ${snapshot.release_id}`}</p>}
      {snapshot?.stale && <p role="status">Snapshot is stale. The host collector has not updated it for over three minutes.</p>}
      {error && <p role="alert">Docker image inventory is unavailable. Try refreshing.</p>}
      <label className="table-tools"><span className="sr-only">Search images</span>
        <input placeholder="Search images or containers" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
      <div className="table-wrap"><table><thead><tr>
        <th><button onClick={() => changeSort("name")}>Image</button></th>
        <th>Use</th><th><button onClick={() => changeSort("size")}>Size</button></th>
        <th><button onClick={() => changeSort("created")}>Created</button></th>
        <th><button onClick={() => changeSort("started")}>Last container start</button></th>
        <th><button onClick={() => changeSort("activated")}>Release activated</button></th>
      </tr></thead><tbody>{images.map((image) => <tr key={image.id}>
        <td data-label="Image"><strong>{image.tags.length ? image.tags.join(", ") : "Untagged"}</strong>
          <small><code>{image.id}</code></small>{image.digests.map((digest) => <small key={digest}><code>{digest}</code></small>)}</td>
        <td data-label="Use">{image.containers.some((container) => container.status === "running") ? "In use" : "Unused"}
          {image.containers.map((container) => <small key={container.name}>{container.name} ({container.status})</small>)}</td>
        <td data-label="Size">{size(image.size_bytes)}</td>
        <td data-label="Created">{date(image.created_at)}</td>
        <td data-label="Last container start">{date(image.last_container_start_at)}</td>
        <td data-label="Release activated">{date(image.release_activated_at)}</td>
      </tr>)}</tbody></table></div>
      {!loading && snapshot && images.length === 0 && <p className="no-results">{snapshot.images.length ? "No images match your search." : "No Docker images are stored on this host."}</p>}
    </section>
  </main>;
}
