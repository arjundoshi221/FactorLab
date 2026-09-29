import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DockerImages } from "./DockerImages";

afterEach(() => vi.unstubAllGlobals());

const commit = "c".repeat(40);
const release = {
  id: "20260925T050000Z-cccccccccccc", commit, image: `ghcr.io/arjundoshi221/factorlab@sha256:${"d".repeat(64)}`,
  previous_release: "20260924T174540Z-0b3003925547", previous_image: "unknown", activated_at: "2026-09-25T05:10:00Z",
};
const snapshot = {
  snapshot_at: "2026-09-25T10:02:00Z", release_id: release.id, stale: false,
  api_build: { release_id: release.id, commit },
  release,
  releases: [release, { ...release, id: "20260924T174540Z-0b3003925547", commit: "b".repeat(40), activated_at: null }],
  images: [
    { id: `sha256:${"1".repeat(64)}`, tags: ["factorlab:latest"], digests: [], size_bytes: 1048576, created_at: "2026-09-20T00:00:00Z",
      containers: [{ name: "factorlab-api-1", service: "api", image_ref: release.image, status: "running", started_at: "2026-09-25T05:01:00Z" }],
      last_container_start_at: "2026-09-25T05:01:00Z", release_activated_at: "2026-09-25T05:10:00Z",
      current_release: true, platform: "linux/amd64", labels: { revision: commit, version: release.id } },
    { id: `sha256:${"2".repeat(64)}`, tags: [], digests: [], size_bytes: 2097152, created_at: "2026-09-21T00:00:00Z",
      containers: [], last_container_start_at: null, release_activated_at: null },
  ],
};

it("shows the running release, service pins, totals, and history", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));
  render(<DockerImages />);
  expect(await screen.findByRole("heading", { name: release.id })).toBeInTheDocument();
  expect(screen.getByText("Verified release")).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: commit.slice(0, 12) })[0]).toHaveAttribute("href", expect.stringContaining(`/commit/${commit}`));
  const services = screen.getByRole("heading", { name: "What each service runs" }).closest("section")!;
  expect(within(services).getByText("api")).toBeInTheDocument();
  expect(within(services).getByText("Current")).toBeInTheDocument();
  expect(screen.getByText("3.0 MB")).toBeInTheDocument();
  expect(screen.getByText("2.0 MB", { selector: "strong" })).toBeInTheDocument();
  const history = screen.getByRole("heading", { name: "Release history" }).closest("section")!;
  expect(within(history).getByText("Running")).toBeInTheDocument();
  expect(within(history).getByText("Not activated")).toBeInTheDocument();
});

it("filters, searches, sorts, and refreshes stored images", async () => {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
  vi.stubGlobal("fetch", fetchMock);
  render(<DockerImages />);
  const images = (await screen.findByRole("heading", { name: "Stored images" })).closest("section")!;
  expect(within(images).getByText("factorlab:latest")).toBeInTheDocument();
  expect(within(images).getByText("Untagged")).toBeInTheDocument();
  expect(within(images).getByText("Current release")).toBeInTheDocument();
  expect(within(images).getByRole("button", { name: "Created" })).toBeInTheDocument();
  expect(within(images).getByRole("button", { name: "Last container start" })).toBeInTheDocument();
  expect(within(images).getByRole("button", { name: "Release activated" })).toBeInTheDocument();

  fireEvent.click(within(images).getByRole("button", { name: "Not running" }));
  expect(within(images).queryByText("factorlab:latest")).not.toBeInTheDocument();
  expect(within(images).getByText("Untagged")).toBeInTheDocument();
  fireEvent.click(within(images).getByRole("button", { name: "All" }));

  fireEvent.change(screen.getByPlaceholderText("Search images, services, or commits"), { target: { value: "api" } });
  expect(within(images).queryByText("Untagged")).not.toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText("Search images, services, or commits"), { target: { value: "no-such-image" } });
  expect(screen.getByText("No images match your filters.")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Refresh images" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh images" })).toBeEnabled());
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("flags an API build that differs from the host release", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    ...snapshot, api_build: { release_id: "20260924T174540Z-0b3003925547", commit: null },
  }) }));
  render(<DockerImages />);
  expect(await screen.findByText("API build differs")).toBeInTheDocument();
  expect(screen.getByText(/The API container reports release/)).toBeInTheDocument();
});

it("shows stale, empty, legacy, and fetch error states", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ snapshot_at: "2026-09-23T10:00:00Z", release_id: null, stale: true, images: [] }) })
    .mockRejectedValueOnce(new Error("offline")));
  render(<DockerImages />);
  expect(await screen.findByText(/Snapshot is stale/)).toBeInTheDocument();
  expect(screen.getByText("No release recorded")).toBeInTheDocument();
  expect(screen.getByText("No Docker images are stored on this host.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh images" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Docker image inventory is unavailable");
});
