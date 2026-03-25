import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import CatalogDashboard from "./CatalogDashboard";

function createJsonResponse(payload) {
  return {
    json: async () => payload,
    ok: true,
  };
}

function createErrorResponse(detail) {
  return {
    json: async () => ({ detail }),
    ok: false,
  };
}

function createDeferredResponse() {
  let resolve;

  return {
    promise: new Promise((resolver) => {
      resolve = resolver;
    }),
    resolve,
  };
}

async function waitFor(assertion, timeout = 2000) {
  const startTime = Date.now();

  while (true) {
    try {
      assertion();
      return;
    } catch (error) {
      if (Date.now() - startTime > timeout) {
        throw error;
      }

      await act(async () => {
        await new Promise((resolve) => {
          setTimeout(resolve, 20);
        });
      });
    }
  }
}

describe("Catalog dashboard page", () => {
  let container;
  let defaultFetchImplementation;
  let fetchMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);

    defaultFetchImplementation = (url) => {
      if (url === "/api/batch/progress") {
        return Promise.resolve(createJsonResponse({
          avg_seconds_per_book: 1200,
          batch_id: "batch_1",
          book_results: [],
          books_completed: 3,
          books_failed: 1,
          books_in_progress: 1,
          books_skipped: 0,
          current_book_id: 44,
          current_book_title: "The Count of Monte Cristo",
          elapsed_seconds: 3600,
          estimated_completion: "2026-03-25T18:00:00Z",
          model_reloads: 2,
          pause_reason: null,
          percent_complete: 60,
          resource_warnings: [],
          started_at: "2026-03-25T12:00:00Z",
          status: "running",
          total_books: 5,
        }));
      }

      if (url === "/api/export/batch/progress") {
        return Promise.resolve(createJsonResponse({
          batch_id: "export_batch_1",
          books: [],
          completed: 2,
          completed_at: null,
          failed: 0,
          formats_requested: ["mp3", "m4b"],
          in_progress: 1,
          include_only_approved: true,
          not_ready: 0,
          queued: 3,
          skipped: 0,
          started_at: "2026-03-25T12:05:00Z",
          status: "running",
          total_books: 3,
        }));
      }

      if (url === "/api/monitoring/resources") {
        return Promise.resolve(createJsonResponse({
          cpu_percent: 18,
          disk_free_gb: 450,
          disk_total_gb: 1000,
          disk_used_percent: 55,
          gpu_memory_mb: null,
          memory_total_mb: 32000,
          memory_used_mb: 12500,
          memory_used_percent: 39,
        }));
      }

      if (url === "/api/monitoring/model") {
        return Promise.resolve(createJsonResponse({
          chapters_generated: 3,
          chunks_generated: 120,
          cooldown_threshold_chapters: 50,
          cooldown_threshold_chunks: 2000,
          reload_count: 1,
        }));
      }

      if (url === "/api/monitoring/model/reload") {
        return Promise.resolve(createJsonResponse({ status: "reloaded" }));
      }

      if (url === "/api/qa/catalog-summary") {
        return Promise.resolve(createJsonResponse({
          books_all_approved: 15,
          books_pending_qa: 2,
          books_with_flags: 1,
          chapters_approved: 40,
          chapters_flagged: 3,
          chapters_pending: 5,
          total_books: 20,
          total_chapters: 48,
        }));
      }

      if (url === "/api/library?sort=updated_at&limit=20") {
        return Promise.resolve(createJsonResponse({
          books: [
            {
              author: "Alexandria",
              chapter_count: 10,
              created_at: "2026-03-24T00:00:00Z",
              export_status: "completed",
              folder_path: "count-of-monte-cristo",
              generation_eta_seconds: null,
              generation_started_at: null,
              generation_status: "idle",
              generation_status_label: "idle",
              id: 44,
              narrator: "Kent Zimering",
              page_count: 500,
              status: "exported",
              subtitle: null,
              title: "The Count of Monte Cristo",
              trim_size: "6x9",
              updated_at: "2026-03-25T12:10:00Z",
            },
          ],
          stats: {
            exported: 9,
            generated: 4,
            generating: 2,
            not_started: 1,
            parsed: 2,
            qa: 1,
            qa_approved: 1,
          },
          total: 1,
        }));
      }

      if (url === "/api/export/batch") {
        return Promise.resolve(createJsonResponse({
          batch_id: "export_batch_2",
          not_ready: 0,
          queued: 3,
          skipped: 1,
          started_at: "2026-03-25T12:12:00Z",
          status: "running",
        }));
      }

      if (url === "/api/batch/estimate") {
        return Promise.resolve(createJsonResponse({
          books: 18,
          can_proceed: true,
          disk_free_gb: 450,
          estimated_audio_hours: 12.5,
          estimated_disk_gb: 2.4,
          estimated_generation_hours: 18.7,
          total_chapters: 48,
          total_words: 180000,
          warnings: ["Skipping 2 books that are already exported."],
        }));
      }

      if (url === "/api/batch/start") {
        return Promise.resolve(createJsonResponse({
          avg_seconds_per_book: 1200,
          batch_id: "batch_2",
          book_results: [],
          books_completed: 0,
          books_failed: 0,
          books_in_progress: 1,
          books_remaining: 17,
          books_skipped: 0,
          current_book_id: 44,
          current_book_title: "The Count of Monte Cristo",
          elapsed_seconds: 0,
          estimated_completion: "2026-03-26T05:00:00Z",
          model_reloads: 0,
          pause_reason: null,
          percent_complete: 0,
          resource_warnings: [],
          scheduling_strategy: "shortest",
          started_at: "2026-03-25T12:15:00Z",
          status: "running",
          summary: "Completed: 0 | Failed: 0 | Skipped: 0 | Remaining: 17",
          total_books: 18,
        }));
      }

      throw new Error(`Unexpected fetch URL: ${url}`);
    };
    fetchMock = jest.fn(defaultFetchImplementation);
    global.fetch = fetchMock;
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  async function renderDashboard() {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <CatalogDashboard />
        </MemoryRouter>,
      );
    });
  }

  test("renders catalog progress, resources, and quick actions", async () => {
    await renderDashboard();

    await waitFor(() => {
      expect(container.textContent).toContain("Catalog Progress");
      expect(container.textContent).toContain("15 / 20 books");
      expect(container.textContent).toContain("75.0% production-ready");
      expect(container.textContent).toContain("Disk");
      expect(container.textContent).toContain("RAM");
      expect(container.textContent).toContain("Batch Export All Ready");
      expect(container.textContent).toContain("The Count of Monte Cristo");
    });
  });

  test("shows a checklist loader while dashboard endpoints settle", async () => {
    const deferredBatch = createDeferredResponse();
    const deferredExport = createDeferredResponse();
    const deferredResources = createDeferredResponse();
    const deferredModel = createDeferredResponse();
    const deferredQa = createDeferredResponse();
    const deferredActivity = createDeferredResponse();

    fetchMock.mockImplementation((url) => {
      if (url === "/api/batch/progress") {
        return deferredBatch.promise;
      }
      if (url === "/api/export/batch/progress") {
        return deferredExport.promise;
      }
      if (url === "/api/monitoring/resources") {
        return deferredResources.promise;
      }
      if (url === "/api/monitoring/model") {
        return deferredModel.promise;
      }
      if (url === "/api/qa/catalog-summary") {
        return deferredQa.promise;
      }
      if (url === "/api/library?sort=updated_at&limit=20") {
        return deferredActivity.promise;
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });

    await renderDashboard();

    expect(container.textContent).toContain("Loading dashboard... (0/6)");
    expect(container.textContent).toContain("Batch progress...");

    deferredBatch.resolve(defaultFetchImplementation("/api/batch/progress"));
    deferredExport.resolve(defaultFetchImplementation("/api/export/batch/progress"));
    deferredResources.resolve(defaultFetchImplementation("/api/monitoring/resources"));
    deferredModel.resolve(defaultFetchImplementation("/api/monitoring/model"));
    deferredQa.resolve(defaultFetchImplementation("/api/qa/catalog-summary"));
    deferredActivity.resolve(defaultFetchImplementation("/api/library?sort=updated_at&limit=20"));

    await waitFor(() => {
      expect(container.textContent).toContain("Catalog Progress");
      expect(container.textContent).toContain("The Count of Monte Cristo");
    });
  });

  test("calls the model reload and batch export endpoints from dashboard controls", async () => {
    await renderDashboard();

    await waitFor(() => {
      expect(container.textContent).toContain("Force Model Reload");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Force Model Reload"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => url === "/api/monitoring/model/reload"),
      ).toBe(true);
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Batch Export All Ready"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => url === "/api/export/batch"),
      ).toBe(true);
      expect(container.textContent).toContain("Queued exports for all ready books.");
    });
  });

  test("shows a batch estimate before starting a new catalog run", async () => {
    fetchMock.mockImplementation((url, options) => {
      if (url === "/api/batch/progress") {
        return Promise.resolve(createJsonResponse({
          avg_seconds_per_book: 0,
          batch_id: "batch_idle",
          book_results: [],
          books_completed: 0,
          books_failed: 0,
          books_in_progress: 0,
          books_remaining: 0,
          books_skipped: 0,
          current_book_id: null,
          current_book_title: null,
          elapsed_seconds: 0,
          estimated_completion: null,
          model_reloads: 0,
          pause_reason: null,
          percent_complete: 0,
          resource_warnings: [],
          scheduling_strategy: "shortest",
          started_at: "2026-03-25T12:00:00Z",
          status: "idle",
          summary: "",
          total_books: 0,
        }));
      }
      return defaultFetchImplementation(url, options);
    });

    await renderDashboard();

    await waitFor(() => {
      expect(container.textContent).toContain("Start Batch");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Start Batch"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => url === "/api/batch/estimate"),
      ).toBe(true);
      expect(container.textContent).toContain("Estimate overnight generation");
      expect(container.textContent).toContain("Skipping 2 books that are already exported.");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "Confirm Batch Start")
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      const startRequest = fetchMock.mock.calls.find(([url]) => url === "/api/batch/start");
      expect(startRequest).toBeTruthy();
      expect(startRequest[1].body).toContain('"scheduling_strategy":"shortest"');
      expect(container.textContent).toContain("Started a new catalog generation batch.");
    });
  });

  test("uses partial dashboard data when one section fails", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/export/batch/progress") {
        return Promise.resolve(createErrorResponse("Export progress unavailable."));
      }
      return defaultFetchImplementation(url);
    });

    await renderDashboard();

    await waitFor(() => {
      expect(container.textContent).toContain("Catalog Progress");
      expect(container.textContent).toContain("The Count of Monte Cristo");
      expect(container.textContent).toContain("Unable to load export progress.");
    });
  });
});
