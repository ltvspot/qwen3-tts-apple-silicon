import React, { act } from "react";
import ReactDOM from "react-dom/client";
import GenerationProgress from "./GenerationProgress";

function createJsonResponse(payload) {
  return {
    json: async () => payload,
    ok: true,
  };
}

function createStatusPayload(overrides = {}) {
  return {
    book_id: 15,
    chapters: [
      {
        audio_duration_seconds: null,
        chapter_n: 1,
        current_chunk: 5,
        error_message: null,
        expected_total_seconds: 30,
        generated_at: null,
        progress_seconds: 10,
        started_at: "2026-03-24T09:00:00Z",
        status: "generating",
        total_chunks: 12,
      },
    ],
    current_chunk: 5,
    current_chapter_n: 1,
    eta_seconds: 20,
    started_at: "2026-03-24T09:00:00Z",
    status: "generating",
    total_chunks: 12,
    ...overrides,
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

describe("GenerationProgress", () => {
  let container;
  let fetchMock;
  let root;

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

  async function renderProgress() {
    await act(async () => {
      root.render(
        <GenerationProgress
          active
          bookId={15}
          chapters={[{ id: 1, number: 1, title: "Chapter One", type: "chapter", word_count: 120 }]}
        />,
      );
    });
  }

  test("uses exponential backoff and clears polling errors after a successful retry", async () => {
    fetchMock
      .mockRejectedValueOnce(new Error("offline"))
      .mockRejectedValueOnce(new Error("still offline"))
      .mockResolvedValueOnce(createJsonResponse(createStatusPayload()));

    await renderProgress();

    await waitFor(() => {
      expect(container.textContent).toContain("Retrying generation status in 2s (1/10)");
    });

    await act(async () => {
      jest.advanceTimersByTime(2000);
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Retrying generation status in 4s (2/10)");
    });

    await act(async () => {
      jest.advanceTimersByTime(4000);
      await Promise.resolve();
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Chunk 5/12");
      expect(container.textContent).not.toContain("Retrying generation status");
    });
  });

  test("shows a retry button after repeated polling failures and restarts polling on click", async () => {
    let attempts = 0;
    fetchMock.mockImplementation(() => {
      attempts += 1;
      if (attempts <= 10) {
        return Promise.reject(new Error("offline"));
      }
      return Promise.resolve(createJsonResponse(createStatusPayload({ status: "idle" })));
    });

    await renderProgress();

    for (let index = 0; index < 9; index += 1) {
      await act(async () => {
        jest.advanceTimersByTime(8000);
        await Promise.resolve();
      });
    }

    await waitFor(() => {
      expect(container.textContent).toContain("Connection lost — click to retry");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Connection lost"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(11);
      expect(container.textContent).not.toContain("Connection lost — click to retry");
    });
  });
});
