import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import Queue from "./Queue";

function createQueueJob(overrides = {}) {
  return {
    avg_seconds_per_chapter: 15.3,
    book_author: "Alexandre Dumas",
    book_id: 15,
    book_title: "The Count of Monte Cristo",
    chapters_completed: 23,
    chapters_failed: 0,
    chapters_total: 117,
    completed_at: null,
    created_at: "2026-03-24T09:00:00Z",
    current_chapter_n: 24,
    current_chapter_title: "Chapter XXIV: The Luncheon",
    error_message: null,
    eta_seconds: 1845,
    job_id: 1,
    job_type: "full_book",
    paused_at: null,
    priority: 10,
    progress_percent: 19.66,
    started_at: "2026-03-24T09:05:00Z",
    status: "generating",
    ...overrides,
  };
}

function createQueuePayload(overrides = {}) {
  return {
    active_job_count: 2,
    jobs: [createQueueJob()],
    queue_stats: {
      estimated_total_time_seconds: 6000,
      total_books_in_queue: 5,
      total_chapters: 400,
    },
    total_count: 1,
    ...overrides,
  };
}

function createJobDetail(overrides = {}) {
  return {
    avg_seconds_per_chapter: 15.3,
    book_id: 15,
    book_title: "The Count of Monte Cristo",
    chapter_breakdown: [
      {
        chapter_n: 24,
        chapter_title: "Chapter XXIV: The Luncheon",
        completed_at: null,
        duration_seconds: null,
        error_message: null,
        expected_total_seconds: 847,
        progress_seconds: 320.5,
        started_at: "2026-03-24T11:45:00Z",
        status: "generating",
      },
    ],
    chapters_completed: 23,
    chapters_failed: 0,
    chapters_total: 117,
    completed_at: null,
    created_at: "2026-03-24T09:00:00Z",
    current_chapter_n: 24,
    error_message: null,
    eta_seconds: 1845,
    history: [
      {
        action: "resumed",
        details: "User resumed from pause.",
        timestamp: "2026-03-24T11:00:00Z",
      },
    ],
    job_id: 1,
    paused_at: null,
    priority: 10,
    started_at: "2026-03-24T09:05:00Z",
    status: "generating",
    ...overrides,
  };
}

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
        jest.advanceTimersByTime(20);
        await Promise.resolve();
      });
    }
  }
}

function setFormValue(element, value, eventName) {
  const prototype = Object.getPrototypeOf(element);
  const valueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;

  valueSetter.call(element, value);
  element.dispatchEvent(new Event(eventName, { bubbles: true }));
}

describe("Queue page", () => {
  let container;
  let root;
  let fetchMock;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);

    fetchMock = jest.fn();
    global.fetch = fetchMock;
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    jest.useRealTimers();
    container.remove();
    delete global.fetch;
  });

  async function renderQueue() {
    await act(async () => {
      root.render(
        <MemoryRouter
          future={{
            v7_relativeSplatPath: true,
            v7_startTransition: true,
          }}
        >
          <Queue />
        </MemoryRouter>,
      );
    });
  }

  test("renders queue stats, opens job details, and polls for updates", async () => {
    fetchMock.mockImplementation((url) => {
      if (String(url).startsWith("/api/queue?")) {
        return Promise.resolve(createJsonResponse(createQueuePayload()));
      }
      if (url === "/api/queue/1") {
        return Promise.resolve(createJsonResponse(createJobDetail()));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    await renderQueue();

    await waitFor(() => {
      expect(container.textContent).toContain("The Count of Monte Cristo");
      expect(container.textContent).toContain("Books In Queue");
      expect(container.textContent).toContain("400");
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/queue?limit=100&offset=0");

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("View Details"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Job Details");
      expect(container.textContent).toContain("Chapter Breakdown");
      expect(container.textContent).toContain("User resumed from pause.");
    });

    await act(async () => {
      jest.advanceTimersByTime(4000);
    });

    await waitFor(() => {
      const queueCalls = fetchMock.mock.calls.filter(([url]) => String(url).startsWith("/api/queue?"));
      expect(queueCalls.length).toBeGreaterThanOrEqual(2);
    });
  });

  test("posts queue actions and refreshes the list", async () => {
    const refreshedJob = createQueueJob({ paused_at: "2026-03-24T12:00:00Z", status: "paused" });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(createQueuePayload()))
      .mockResolvedValueOnce(createJsonResponse({ job_id: 1, paused_at: "2026-03-24T12:00:00Z", status: "paused" }))
      .mockResolvedValueOnce(createJsonResponse(createQueuePayload({ jobs: [refreshedJob] })));

    await renderQueue();

    await waitFor(() => {
      expect(container.textContent).toContain("Pause");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Pause"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls[1][0]).toBe("/api/queue/1/pause");
      expect(fetchMock.mock.calls[1][1].method).toBe("POST");
      expect(container.textContent).toContain("Paused");
      expect(container.textContent).toContain("Resume");
    });
  });

  test("opens the batch dialog and queues all parsed books", async () => {
    fetchMock
      .mockResolvedValueOnce(createJsonResponse(createQueuePayload()))
      .mockResolvedValueOnce(createJsonResponse({
        books_queued: 2,
        estimated_completion_seconds: 27630,
        jobs_created: 2,
        message: "All 2 parsed books queued for generation",
        total_chapters: 1842,
      }))
      .mockResolvedValueOnce(createJsonResponse(createQueuePayload()));

    await renderQueue();

    await waitFor(() => {
      expect(container.textContent).toContain("Generate All Parsed Books");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Generate All Parsed Books"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Queue all parsed books");
    });

    const priorityInput = container.querySelector('input[type="number"]');
    await act(async () => {
      setFormValue(priorityInput, "25", "input");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Confirm Batch Queue"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls[1][0]).toBe("/api/queue/batch-all");
      expect(fetchMock.mock.calls[1][1].method).toBe("POST");
      expect(fetchMock.mock.calls[1][1].body).toContain('"priority":25');
      expect(container.textContent).toContain("All 2 parsed books queued for generation");
    });
  });
});
