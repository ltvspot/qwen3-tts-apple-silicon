import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import BatchProduction from "./BatchProduction";

function createJsonResponse(payload, options = {}) {
  return {
    json: async () => payload,
    ok: options.ok ?? true,
    status: options.status ?? 200,
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

describe("Batch Production page", () => {
  let container;
  let fetchMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);
    fetchMock = jest.fn((url) => {
      if (url === "/api/batch/active") {
        return Promise.resolve(createJsonResponse({
          batch_id: "batch_active",
          status: "running",
          current_book_title: "Walden",
          estimated_completion: "2026-03-27T18:00:00Z",
          books_completed: 2,
          books_failed: 0,
          books_remaining: 3,
          percent_complete: 40,
          book_results: [
            {
              book_id: 1,
              title: "Walden",
              status: "completed",
              completed_at: "2026-03-27T12:00:00Z",
              qa_average_score: 92.5,
              qa_ready_for_export: true,
            },
          ],
        }));
      }
      if (url === "/api/system/resources") {
        return Promise.resolve(createJsonResponse({
          disk_free_gb: 22.5,
          memory_used_percent: 41.0,
          throughput_chapters_per_hour: 7.5,
          output_directory_size_gb: 4.2,
        }));
      }
      if (url === "/api/system/model-status") {
        return Promise.resolve(createJsonResponse({
          chapters_since_restart: 12,
          restart_interval: 50,
          memory_usage_mb: 1536,
          model_loaded: true,
        }));
      }
      if (url === "/api/batch/start") {
        return Promise.resolve(createJsonResponse({ status: "running" }));
      }
      if (url === "/api/batch/batch_active/pause") {
        return Promise.resolve(createJsonResponse({ status: "paused" }));
      }
      throw new Error(`Unexpected fetch URL: ${url}`);
    });
    global.fetch = fetchMock;
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  async function renderPage() {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <BatchProduction />
        </MemoryRouter>,
      );
    });
  }

  test("renders active batch, resources, and model restart status", async () => {
    await renderPage();

    await waitFor(() => {
      expect(container.textContent).toContain("Batch running");
      expect(container.textContent).toContain("Walden");
      expect(container.textContent).toContain("22.5 GB free");
      expect(container.textContent).toContain("12 / 50 chapters since restart");
      expect(container.textContent).toContain("Output footprint");
      expect(document.title).toBe("Batch Production | Alexandria Audiobook Narrator");
    });
  });

  test("can start and pause batch production from the page", async () => {
    await renderPage();

    await waitFor(() => {
      expect(container.textContent).toContain("Start Batch");
      expect(container.textContent).toContain("Pause");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Start Batch"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => url === "/api/batch/start")).toBe(true);
      expect(container.textContent).toContain("Started a new production batch.");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Pause"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => url === "/api/batch/batch_active/pause")).toBe(true);
    });
  });
});
