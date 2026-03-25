import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import QA from "./QA";

function createCheck(name, status, message, value = null) {
  return {
    message,
    name,
    status,
    value,
  };
}

function createChapter(overrides = {}) {
  return {
    audio_url: "/api/book/15/chapter/1/audio",
    automatic_checks: [
      createCheck("file_exists", "pass", "File exists (1024 bytes).", 1024),
      createCheck("contextual_silence", "warning", "Synthetic warning.", 4.2),
    ],
    book_id: 15,
    chapter_n: 1,
    chapter_title: "Chapter One",
    chapter_type: "chapter",
    checked_at: "2026-03-24T12:00:00Z",
    manual_notes: null,
    manual_reviewed_at: null,
    manual_reviewed_by: null,
    manual_status: null,
    overall_status: "warning",
    qa_grade: "B",
    ...overrides,
  };
}

function createBook(overrides = {}) {
  const chapters = overrides.chapters ?? [createChapter()];

  return {
    book_author: "Alexandria",
    book_id: 15,
    book_title: "Warning Book",
    chapters,
    chapters_fail: chapters.filter((chapter) => chapter.overall_status === "fail").length,
    chapters_pass: chapters.filter((chapter) => chapter.overall_status === "pass").length,
    chapters_pending_manual: chapters.filter(
      (chapter) =>
        chapter.manual_status === null &&
        (chapter.overall_status === "warning" || chapter.overall_status === "fail"),
    ).length,
    chapters_total: chapters.length,
    chapters_warning: chapters.filter((chapter) => chapter.overall_status === "warning").length,
    latest_checked_at: "2026-03-24T12:00:00Z",
    overall_book_status: "warning",
    ...overrides,
  };
}

function createDashboardPayload(overrides = {}) {
  const warningBook = createBook();
  const cleanBook = createBook({
    book_id: 16,
    book_title: "Clean Book",
    chapters: [
      createChapter({
        audio_url: "/api/book/16/chapter/1/audio",
        book_id: 16,
        manual_status: null,
        overall_status: "pass",
      }),
    ],
    overall_book_status: "pass",
  });
  const books = overrides.books_needing_review ?? [warningBook, cleanBook];

  return {
    books_needing_review: books,
    summary: {
      books_reviewed: books.length,
      chapters_fail: books.reduce((total, book) => total + book.chapters_fail, 0),
      chapters_pass: books.reduce((total, book) => total + book.chapters_pass, 0),
      chapters_pending_manual: books.reduce((total, book) => total + book.chapters_pending_manual, 0),
      chapters_reviewed: books.reduce((total, book) => total + book.chapters.length, 0),
      chapters_warning: books.reduce((total, book) => total + book.chapters_warning, 0),
    },
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
        await new Promise((resolve) => {
          setTimeout(resolve, 20);
        });
      });
    }
  }
}

function setFormValue(element, value, eventName = "change") {
  const prototype = Object.getPrototypeOf(element);
  const valueSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;

  valueSetter.call(element, value);
  element.dispatchEvent(new Event(eventName, { bubbles: true }));
}

describe("QA dashboard page", () => {
  let container;
  let fetchMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
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

    container.remove();
    delete global.fetch;
  });

  async function renderQA() {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <QA />
        </MemoryRouter>,
      );
    });
  }

  test("renders QA data, filters by pending review, expands chapters, and reveals checks", async () => {
    fetchMock.mockResolvedValueOnce(createJsonResponse(createDashboardPayload()));

    await renderQA();

    await waitFor(() => {
      expect(container.textContent).toContain("Warning Book");
      expect(container.textContent).toContain("Clean Book");
      expect(container.textContent).toContain("Pending Manual");
    });

    const selects = Array.from(container.querySelectorAll("select"));
    await act(async () => {
      setFormValue(selects[0], "pending_review");
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Warning Book");
      expect(container.querySelector('[data-book-qa="16"]')).toBeNull();
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Review Chapters"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Chapter One");
      expect(container.textContent).toContain("Awaiting manual QA");
      expect(container.textContent).toContain("Grade B");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Show Checks"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("contextual silence");
      expect(container.textContent).toContain("Synthetic warning.");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Listen"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.querySelector("audio")).not.toBeNull();
    });
  });

  test("approves a chapter and refreshes the dashboard state", async () => {
    const approvedChapter = createChapter({
      manual_reviewed_at: "2026-03-24T12:10:00Z",
      manual_reviewed_by: "Tim",
      manual_status: "approved",
      overall_status: "warning",
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(createDashboardPayload({
        books_needing_review: [createBook()],
      })))
      .mockResolvedValueOnce(createJsonResponse({
        book_id: 15,
        chapter_n: 1,
        manual_notes: null,
        manual_reviewed_at: "2026-03-24T12:10:00Z",
        manual_reviewed_by: "Tim",
        manual_status: "approved",
      }))
      .mockResolvedValueOnce(createJsonResponse(createDashboardPayload({
        books_needing_review: [createBook({
          chapters: [approvedChapter],
          chapters_pending_manual: 0,
          overall_book_status: "warning",
        })],
      })));

    await renderQA();

    await waitFor(() => {
      expect(container.textContent).toContain("Warning Book");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Review Chapters"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Approve");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.trim() === "Approve")
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls[1][0]).toBe("/api/book/15/chapter/1/qa");
      expect(fetchMock.mock.calls[1][1].method).toBe("POST");
      expect(container.textContent).toContain("Approved by Tim");
    });
  });

  test("approves all passing chapters for a book", async () => {
    const passChapter = createChapter({
      book_id: 15,
      chapter_n: 1,
      manual_status: null,
      overall_status: "pass",
    });
    const warningChapter = createChapter({
      book_id: 15,
      chapter_n: 2,
      overall_status: "warning",
    });
    const refreshedPassChapter = createChapter({
      book_id: 15,
      chapter_n: 1,
      manual_reviewed_at: "2026-03-24T12:20:00Z",
      manual_reviewed_by: "auto-approved",
      manual_status: "approved",
      overall_status: "pass",
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(createDashboardPayload({
        books_needing_review: [createBook({
          chapters: [passChapter, warningChapter],
          chapters_pass: 1,
          chapters_pending_manual: 1,
          chapters_warning: 1,
          overall_book_status: "warning",
        })],
      })))
      .mockResolvedValueOnce(createJsonResponse({ approved: 1 }))
      .mockResolvedValueOnce(createJsonResponse(createDashboardPayload({
        books_needing_review: [createBook({
          chapters: [refreshedPassChapter, warningChapter],
          chapters_pass: 1,
          chapters_pending_manual: 1,
          chapters_warning: 1,
          overall_book_status: "warning",
        })],
      })));

    await renderQA();

    await waitFor(() => {
      expect(container.textContent).toContain("Warning Book");
      expect(container.textContent).toContain("Approve All Passing");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Approve All Passing"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls[1][0]).toBe("/api/book/15/approve-all-passing");
      expect(fetchMock.mock.calls[1][1].method).toBe("POST");
      expect(container.textContent).toContain("Approved 1 passing chapter for Warning Book.");
    });
  });

  test("opens the flag modal, captures notes, and saves a flagged review", async () => {
    const flaggedChapter = createChapter({
      manual_notes: "Long silence after paragraph three.",
      manual_reviewed_at: "2026-03-24T12:15:00Z",
      manual_reviewed_by: "Tim",
      manual_status: "flagged",
      overall_status: "fail",
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(createDashboardPayload({
        books_needing_review: [createBook()],
      })))
      .mockResolvedValueOnce(createJsonResponse({
        book_id: 15,
        chapter_n: 1,
        manual_notes: "Long silence after paragraph three.",
        manual_reviewed_at: "2026-03-24T12:15:00Z",
        manual_reviewed_by: "Tim",
        manual_status: "flagged",
      }))
      .mockResolvedValueOnce(createJsonResponse(createDashboardPayload({
        books_needing_review: [createBook({
          chapters: [flaggedChapter],
          chapters_fail: 1,
          chapters_pending_manual: 0,
          overall_book_status: "fail",
        })],
      })));

    await renderQA();

    await waitFor(() => {
      expect(container.textContent).toContain("Warning Book");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Review Chapters"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Flag"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Flag Chapter 1");
    });

    const textarea = container.querySelector('textarea[name="flag-notes"]');
    await act(async () => {
      setFormValue(textarea, "Long silence after paragraph three.", "input");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Save Flag"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock.mock.calls[1][0]).toBe("/api/book/15/chapter/1/qa");
      expect(container.textContent).toContain("Flagged by Tim");
      expect(container.textContent).toContain("Long silence after paragraph three.");
    });
  });
});
