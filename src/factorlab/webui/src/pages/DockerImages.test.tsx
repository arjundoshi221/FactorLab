import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DockerImages } from "./DockerImages";

afterEach(() => vi.unstubAllGlobals());

it("shows tagged and untagged images with separate dates, search, and refresh", async () => {
  const snapshot = { snapshot_at: "2026-09-23T10:02:00Z", release_id: "r1", stale: false, images: [
    { id: "sha256:one", tags: ["factorlab:latest"], digests: [], size_bytes: 1048576, created_at: "2026-09-20T00:00:00Z", containers: [{ name: "api", status: "running", started_at: "2026-09-23T10:01:00Z" }], last_container_start_at: "2026-09-23T10:01:00Z", release_activated_at: "2026-09-23T10:00:00Z" },
    { id: "sha256:two", tags: [], digests: [], size_bytes: 2097152, created_at: "2026-09-21T00:00:00Z", containers: [], last_container_start_at: null, release_activated_at: null },
  ] };
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
  vi.stubGlobal("fetch", fetchMock);
  render(<DockerImages />);
  expect(await screen.findByText("factorlab:latest")).toBeInTheDocument();
  expect(screen.getByText("Untagged")).toBeInTheDocument();
  expect(screen.getByText("In use")).toBeInTheDocument();
  expect(screen.getByText("Unused")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Created" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Last container start" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Release activated" })).toBeInTheDocument();
  fireEvent.change(screen.getByPlaceholderText("Search images or containers"), { target: { value: "api" } });
  expect(screen.queryByText("Untagged")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh images" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Refresh images" })).toBeEnabled());
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("shows stale, empty, and fetch error states", async () => {
  vi.stubGlobal("fetch", vi.fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ snapshot_at: "2026-09-23T10:00:00Z", release_id: null, stale: true, images: [] }) })
    .mockRejectedValueOnce(new Error("offline")));
  render(<DockerImages />);
  expect(await screen.findByText(/Snapshot is stale/)).toBeInTheDocument();
  expect(screen.getByText("No Docker images are stored on this host.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Refresh images" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Docker image inventory is unavailable");
});
