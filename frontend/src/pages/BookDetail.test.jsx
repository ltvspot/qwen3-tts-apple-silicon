import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import BookDetail from "./BookDetail";

const mockNavigate = jest.fn();

jest.mock("react-router-dom", () => {
  const actual = jest.requireActual("react-router-dom");

  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

function createBook(overrides) {
  return {
    author: "Jane Doe",
    chapter_count: 3,
    created_at: "2026-03-24T00:00:00+00:00",
    folder_path: "1007-test-chronicle",
    generation_eta_seconds: null,
    generation_started_at: null,
    generation_status: "idle",
    id: 7,
    narrator: "Kent Zimering",
    page_count: 145,
    status: "parsed",
    subtitle: "A Test Story",
    title: "The Test Chronicle",
    trim_size: "6x9",
    updated_at: "2026-03-24T00:00:00+00:00",
    ...overrides,
  };
}

function createChapter(overrides) {
  return {
    audio_path: null,
    audio_file_size_bytes: null,
    book_id: 7,
    completed_at: null,
    created_at: "2026-03-24T00:00:00+00:00",
    duration_seconds: null,
    error_message: null,
    id: 701,
    number: 0,
    qa_notes: null,
    qa_status: "not_reviewed",
    status: "pending",
    started_at: null,
    text_content: "Opening credits text.",
    title: "Opening Credits",
    type: "opening_credits",
    updated_at: "2026-03-24T00:00:00+00:00",
    word_count: 3,
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

function createStatusSnapshot(overrides = {}) {
  return {
    book_id: 7,
    chapters: [],
    current_chapter_n: null,
    eta_seconds: null,
    started_at: null,
    status: "idle",
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
        await new Promise((resolve) => {
          setTimeout(resolve, 20);
        });
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

describe("BookDetail page", () => {
  let alertMock;
  let confirmMock;
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
    mockNavigate.mockReset();

    alertMock = jest.spyOn(window, "alert").mockImplementation(() => {});
    confirmMock = jest.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(async () => {
    alertMock.mockRestore();
    confirmMock.mockRestore();

    await act(async () => {
      root.unmount();
    });

    container.remove();
    delete global.fetch;
  });

  async function renderBookDetail() {
    await act(async () => {
      root.render(
        <MemoryRouter
          initialEntries={["/book/7"]}
          future={{
            v7_relativeSplatPath: true,
            v7_startTransition: true,
          }}
        >
          <Routes>
            <Route element={<BookDetail />} path="/book/:id" />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  function getButtonByText(label) {
    return Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent.includes(label),
    );
  }

  test("loads book data, selects the first chapter, and navigates back to the library", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({}),
      createChapter({
        id: 702,
        number: 2,
        text_content: "Chapter two body text.",
        title: "The Beginning",
        type: "chapter",
        word_count: 4,
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("The Test Chronicle");
      expect(container.textContent).toContain("Opening credits text.");
    });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/book/7");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/book/7/chapters");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/book/7/status");
    expect(container.textContent).toContain("A Test Story");
    expect(container.textContent).toContain("Jane Doe");
    expect(container.textContent).toContain("Narrated by Kent Zimering");

    await act(async () => {
      getButtonByText("Back to Library").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mockNavigate).toHaveBeenCalledWith("/");
  });

  test("edits and saves chapter text through the chapter update API", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({}),
      createChapter({
        id: 702,
        number: 2,
        text_content: "Original chapter body.",
        title: "The Beginning",
        type: "chapter",
        word_count: 3,
      }),
    ];
    const updatedChapter = createChapter({
      id: 702,
      number: 2,
      text_content: "Revised chapter text for testing now.",
      title: "The Beginning",
      type: "chapter",
      word_count: 6,
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(updatedChapter));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Opening credits text.");
    });

    await act(async () => {
      container
        .querySelector('[data-chapter-id="702"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Original chapter body.");
    });

    await act(async () => {
      getButtonByText("Edit Chapter").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const textarea = container.querySelector('textarea[aria-label="Chapter text editor"]');
    await act(async () => {
      setFormValue(textarea, updatedChapter.text_content, "input");
    });

    await act(async () => {
      getButtonByText("Save Changes").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(4);
      expect(container.textContent).toContain(updatedChapter.text_content);
      expect(container.textContent).toContain("6 words");
    });

    expect(fetchMock.mock.calls[3][0]).toBe("/api/book/7/chapter/2/text");
    expect(fetchMock.mock.calls[3][1]).toMatchObject({
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
    });
    expect(fetchMock.mock.calls[3][1].body).toBe(
      JSON.stringify({ text_content: updatedChapter.text_content }),
    );
  });

  test("persists narration settings and confirms before discarding unsaved edits", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({}),
      createChapter({
        id: 702,
        number: 2,
        text_content: "Chapter two body text.",
        title: "The Beginning",
        type: "chapter",
        word_count: 4,
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Opening credits text.");
    });

    await act(async () => {
      getButtonByText("Dramatic Reading").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const emotionInput = container.querySelector("#emotion-input");
    expect(emotionInput.value).toBe("dramatic");
    expect(container.textContent).toContain("0.95x playback");

    await act(async () => {
      getButtonByText("Edit Chapter").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    const textarea = container.querySelector('textarea[aria-label="Chapter text editor"]');
    await act(async () => {
      setFormValue(textarea, "Unsaved rewrite for opening credits.", "input");
    });

    confirmMock.mockReturnValueOnce(false);
    await act(async () => {
      container
        .querySelector('[data-chapter-id="702"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(window.confirm).toHaveBeenCalledWith("Discard unsaved chapter edits?");
    expect(container.textContent).toContain("Unsaved changes");
    expect(container.textContent).toContain("Opening Credits");

    confirmMock.mockReturnValueOnce(true);
    await act(async () => {
      container
        .querySelector('[data-chapter-id="702"]')
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Chapter two body text.");
    });

    expect(emotionInput.value).toBe("dramatic");
  });

  test("renders a not-found state when the book record is missing", async () => {
    fetchMock.mockResolvedValueOnce(
      createJsonResponse({ detail: "Book 7 not found" }, { ok: false, status: 404 }),
    );

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Book not found");
    });

    await act(async () => {
      getButtonByText("Back to Library").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mockNavigate).toHaveBeenCalledWith("/");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  test("queues generate all after confirmation", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({}),
      createChapter({
        id: 702,
        number: 1,
        text_content: "Chapter one body text.",
        title: "The Beginning",
        type: "chapter",
        word_count: 4,
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse({
        book_id: 7,
        job_id: 91,
        message: "Book 7 queued for generation",
        status: "queued",
      }))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Generate All");
    });

    await act(async () => {
      getButtonByText("Generate All").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/book/7/generate-all",
        expect.objectContaining({ method: "POST" }),
      );
    });

    expect(window.confirm).toHaveBeenCalledWith(
      "This will generate all remaining chapters. Continue?",
    );
  });

  test("renders completed chapter controls and opens the bottom audio player", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({
        audio_path: "7-the-test-chronicle/chapters/00-opening-credits.wav",
        audio_file_size_bytes: 19000000,
        completed_at: "2026-03-24T00:00:47+00:00",
        duration_seconds: 252,
        id: 701,
        status: "generated",
        title: "Opening Credits",
      }),
    ];
    const statusSnapshot = createStatusSnapshot({
      chapters: [
        {
          audio_duration_seconds: 252,
          audio_file_size_bytes: 19000000,
          chapter_n: 0,
          error_message: null,
          generated_at: "2026-03-24T00:00:47+00:00",
          generation_seconds: 47.2,
          expected_total_seconds: 23.4,
          progress_seconds: null,
          started_at: "2026-03-24T00:00:00+00:00",
          status: "completed",
        },
      ],
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(statusSnapshot));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Re-generate");
      expect(container.textContent).toContain("Preview Audio");
    });

    await act(async () => {
      getButtonByText("Preview Audio").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Chapter Audio Player");
      expect(container.textContent).toContain("Generated in 47.2s");
      expect(container.textContent).toContain("18.1 MB WAV");
    });
  });
});
