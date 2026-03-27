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
    current_chunk: null,
    current_chapter_n: null,
    eta_seconds: null,
    started_at: null,
    status: "idle",
    total_chunks: null,
    ...overrides,
  };
}

function createExportSnapshot(overrides = {}) {
  return {
    book_id: 7,
    completed_at: null,
    current_chapter_n: null,
    current_format: null,
    current_stage: null,
    error_message: null,
    export_status: "idle",
    formats: {},
    job_id: null,
    progress_percent: 0,
    qa_report: null,
    started_at: null,
    total_chapters: null,
    ...overrides,
  };
}

function createVoiceListPayload(overrides = {}) {
  return {
    engine: "qwen3_tts",
    voices: [
      { display_name: "Ethan", is_cloned: false, name: "Ethan" },
      { display_name: "Nova", is_cloned: false, name: "Nova" },
      { display_name: "Aria", is_cloned: false, name: "Aria" },
    ],
    ...overrides,
  };
}

function createBookQualityReport(overrides = {}) {
  return {
    book_id: 7,
    chapters_grade_a: 2,
    chapters_grade_b: 0,
    chapters_grade_c: 0,
    chapters_grade_f: 0,
    cross_chapter_checks: {
      acx_compliance: {
        message: "All chapters satisfy ACX/Audible requirements.",
        status: "pass",
        violations: [],
      },
      chapter_transitions: {
        issues: [],
        message: "Chapter transitions are smooth and properly padded.",
        status: "pass",
      },
      loudness_consistency: {
        max_deviation_lu: 0.8,
        mean_lufs: -20.0,
        message: "All chapters are within the allowed loudness spread.",
        status: "pass",
      },
      pacing_consistency: {
        max_deviation_pct: 6.2,
        mean_wpm: 154.0,
        message: "Cross-chapter pacing is consistent.",
        status: "pass",
      },
      voice_consistency: {
        message: "Voice fingerprint remains consistent across the book.",
        outlier_chapters: [],
        status: "pass",
      },
    },
    export_blockers: [],
    overall_grade: "A",
    ready_for_export: true,
    recommendations: ["All chapters within ACX loudness range."],
    title: "The Test Chronicle",
    total_chapters: 2,
    ...overrides,
  };
}

function createVoiceConsistencyChart(overrides = {}) {
  return {
    book_median: {
      brightness: 2300,
      pitch: 142.5,
      rate: 154,
    },
    chapters: [
      { brightness: 2300, grade: "A", number: 1, pitch: 142.0, rate: 154.0 },
      { brightness: 2310, grade: "A", number: 2, pitch: 143.0, rate: 155.0 },
    ],
    outlier_chapters: [],
    ...overrides,
  };
}

function createDeepQaReport(overrides = {}) {
  return {
    average_quality_score: 94.0,
    average_score: 97.1,
    average_timing_score: 96.0,
    average_transcription_score: 100.0,
    book_id: 7,
    chapter_count: 2,
    chapters: [
      {
        audio_path: "/tmp/chapter-1.wav",
        book_id: 7,
        chapter_id: 701,
        chapter_n: 1,
        chapter_title: "Chapter One",
        checked_at: "2026-03-24T10:00:00+00:00",
        issues: [
          {
            category: "transcription",
            code: "segment_mismatch",
            details: {},
            end_time_seconds: 2.2,
            message: "Transcript segment drift detected.",
            severity: "warning",
            start_time_seconds: 1.5,
          },
        ],
        quality: {
          artifact_events: [],
          clipping_ratio: 0.0,
          dependency: { available: true, dependency: "pyloudnorm", message: null },
          integrated_lufs: -20.0,
          issues: [],
          loudness_range_lu: 4.0,
          peak_dbfs: -6.0,
          score: 94.0,
          snr_db: 28.0,
          status: "pass",
        },
        ready_for_export: true,
        scoring: {
          grade: "A",
          overall: 97.1,
          quality: 94.0,
          reasoning: [],
          status: "pass",
          timing: 96.0,
          transcription: 100.0,
        },
        summary: "Deep audio QA passed with grade A.",
        timing: {
          actual_duration_seconds: 6.0,
          dependency: { available: true, dependency: "librosa", message: null },
          estimated_duration_seconds: 5.8,
          issues: [],
          pause_ratio: 0.05,
          pauses: [],
          score: 96.0,
          speech_rate_wpm: 140.0,
          status: "pass",
        },
        transcription: {
          dependency: { available: true, dependency: "mlx-whisper", message: null },
          diff: [
            {
              actual: "chapter two",
              end_time_seconds: null,
              expected: "chapter one",
              operation: "replace",
              start_time_seconds: null,
            },
          ],
          issues: [],
          model_name: "mlx-community/whisper-tiny",
          normalized_reference: "hello world from chapter one",
          normalized_transcript: "hello world from chapter two",
          provider: "mlx-whisper",
          reference_word_count: 5,
          score: 100.0,
          status: "pass",
          transcript: "hello world from chapter two",
          transcript_word_count: 5,
          word_error_rate: 0.0,
        },
      },
      {
        audio_path: "/tmp/chapter-2.wav",
        book_id: 7,
        chapter_id: 702,
        chapter_n: 2,
        chapter_title: "Chapter Two",
        checked_at: "2026-03-24T10:02:00+00:00",
        issues: [],
        quality: {
          artifact_events: [],
          clipping_ratio: 0.0,
          dependency: { available: true, dependency: "pyloudnorm", message: null },
          integrated_lufs: -20.2,
          issues: [],
          loudness_range_lu: 4.0,
          peak_dbfs: -6.2,
          score: 94.0,
          snr_db: 27.0,
          status: "pass",
        },
        ready_for_export: true,
        scoring: {
          grade: "A",
          overall: 97.1,
          quality: 94.0,
          reasoning: [],
          status: "pass",
          timing: 96.0,
          transcription: 100.0,
        },
        summary: "Deep audio QA passed with grade A.",
        timing: {
          actual_duration_seconds: 5.8,
          dependency: { available: true, dependency: "librosa", message: null },
          estimated_duration_seconds: 5.7,
          issues: [],
          pause_ratio: 0.04,
          pauses: [],
          score: 96.0,
          speech_rate_wpm: 142.0,
          status: "pass",
        },
        transcription: {
          dependency: { available: true, dependency: "mlx-whisper", message: null },
          diff: [],
          issues: [],
          model_name: "mlx-community/whisper-tiny",
          normalized_reference: "chapter two body text",
          normalized_transcript: "chapter two body text",
          provider: "mlx-whisper",
          reference_word_count: 4,
          score: 100.0,
          status: "pass",
          transcript: "chapter two body text",
          transcript_word_count: 4,
          word_error_rate: 0.0,
        },
      },
    ],
    generated_at: "2026-03-24T10:05:00+00:00",
    grade_counts: { A: 2 },
    issue_count: 1,
    ready_for_export: true,
    status_counts: { pass: 2 },
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
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("The Test Chronicle");
      expect(container.textContent).toContain("Opening credits text.");
    });

    expect(document.title).toBe("The Test Chronicle | Alexandria Audiobook Narrator");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/book/7");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/book/7/chapters");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/book/7/status");
    expect(fetchMock.mock.calls[3][0]).toBe("/api/book/7/export/status");
    expect(fetchMock.mock.calls[4][0]).toBe("/api/voice-lab/voices");
    expect(container.textContent).toContain("A Test Story");
    expect(container.textContent).toContain("Jane Doe");
    expect(container.textContent).toContain("Narrated by Kent Zimering");

    await act(async () => {
      getButtonByText("Back to Library").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mockNavigate).toHaveBeenCalledWith("/");
  });

  test("renders book data while voice options continue loading in the background", async () => {
    const book = createBook({});
    const chapters = [createChapter({})];
    let resolveVoices;
    const pendingVoices = new Promise((resolve) => {
      resolveVoices = resolve;
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockImplementationOnce(() => pendingVoices);

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("The Test Chronicle");
      expect(container.textContent).toContain("Opening credits text.");
      expect(container.textContent).toContain("Loading voices...");
      expect(container.textContent).not.toContain("Loading book details...");
    });

    await act(async () => {
      resolveVoices(createJsonResponse(createVoiceListPayload()));
    });
  });

  test("renders an inline audio preview player for generated chapters", async () => {
    const book = createBook({});
    const chapters = [
      createChapter({
        audio_path: "7-the-test-chronicle/chapters/00-opening-credits.wav",
        duration_seconds: 252,
        status: "generated",
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot({
        chapters: [
          {
            audio_duration_seconds: 252,
            audio_file_size_bytes: 19000000,
            chapter_n: 0,
            error_message: null,
            generated_at: "2026-03-24T00:00:47+00:00",
            generation_seconds: 47.2,
            progress_seconds: null,
            started_at: "2026-03-24T00:00:00+00:00",
            status: "completed",
          },
        ],
      })))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Audio Preview");
      expect(container.textContent).toContain("Duration: 4m 12s");
    });

    const audioElement = container.querySelector("audio");
    expect(audioElement).not.toBeNull();
    expect(audioElement.getAttribute("src")).toBe("/api/book/7/chapter/0/preview");
  });

  test("retries voice loading while the engine is still warming up", async () => {
    jest.useFakeTimers();
    const book = createBook({});
    const chapters = [createChapter({})];
    let voiceRequests = 0;

    fetchMock.mockImplementation((url) => {
      if (url === "/api/book/7") {
        return Promise.resolve(createJsonResponse(book));
      }
      if (url === "/api/book/7/chapters") {
        return Promise.resolve(createJsonResponse(chapters));
      }
      if (url === "/api/book/7/status") {
        return Promise.resolve(createJsonResponse(createStatusSnapshot()));
      }
      if (url === "/api/book/7/export/status") {
        return Promise.resolve(createJsonResponse(createExportSnapshot()));
      }
      if (url === "/api/voice-lab/voices") {
        voiceRequests += 1;
        if (voiceRequests === 1) {
          return Promise.resolve(createJsonResponse({
            engine: "qwen3_tts",
            loading: true,
            voices: [],
          }));
        }
        return Promise.resolve(createJsonResponse(createVoiceListPayload()));
      }

      throw new Error(`Unhandled fetch: ${url}`);
    });

    await renderBookDetail();
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain("retrying in 3s (attempt 1/20)");

    await act(async () => {
      jest.advanceTimersByTime(3000);
      await Promise.resolve();
    });

    expect(container.textContent).not.toContain("retrying in 3s");

    expect(voiceRequests).toBe(2);
    jest.useRealTimers();
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
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()))
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
      expect(fetchMock).toHaveBeenCalledTimes(6);
      expect(container.textContent).toContain(updatedChapter.text_content);
      expect(container.textContent).toContain("6 words");
    });

    expect(fetchMock.mock.calls[5][0]).toBe("/api/book/7/chapter/2/text");
    expect(fetchMock.mock.calls[5][1]).toMatchObject({
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
    });
    expect(fetchMock.mock.calls[5][1].body).toBe(
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
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(
        createJsonResponse(
          createVoiceListPayload({
            voices: [
              { display_name: "Ethan", is_cloned: false, name: "Ethan" },
              { display_name: "Kent Zimering Clone", is_cloned: true, name: "kent-zimering" },
            ],
          }),
        ),
      );

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Opening credits text.");
    });

    const voiceSelect = container.querySelector('select[aria-label="Narration voice"]');
    await act(async () => {
      setFormValue(voiceSelect, "kent-zimering", "change");
    });

    expect(voiceSelect.value).toBe("kent-zimering");

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
    expect(voiceSelect.value).toBe("kent-zimering");
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
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()))
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

  test("starts an export and renders completed download cards", async () => {
    const clipboardWriteMock = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(window.navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: clipboardWriteMock,
      },
    });

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
    const completedExportSnapshot = createExportSnapshot({
      completed_at: "2026-03-24T00:10:00+00:00",
      export_status: "completed",
      formats: {
        mp3: {
          completed_at: "2026-03-24T00:10:00+00:00",
          download_url: "/api/book/7/export/download/mp3",
          error_message: null,
          file_name: "The Test Chronicle.mp3",
          file_size_bytes: 487923048,
          status: "completed",
        },
        m4b: {
          completed_at: "2026-03-24T00:10:00+00:00",
          download_url: "/api/book/7/export/download/m4b",
          error_message: null,
          file_name: "The Test Chronicle.m4b",
          file_size_bytes: 341234056,
          status: "completed",
        },
      },
      job_id: "export_7_20260324_001000",
      qa_report: {
        book_id: 7,
        book_title: "The Test Chronicle",
        chapter_summary: [],
        chapters_approved: 1,
        chapters_flagged: 0,
        chapters_included: 1,
        chapters_warnings: 0,
        export_approved: true,
        export_date: "2026-03-24T00:10:00+00:00",
        notes: "All selected chapters exported without QA exclusions.",
      },
    });

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(statusSnapshot))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()))
      .mockResolvedValueOnce(createJsonResponse({
        book_id: 7,
        export_status: "processing",
        expected_completion_seconds: 30,
        formats_requested: ["mp3", "m4b"],
        job_id: "export_7_20260324_001000",
        started_at: "2026-03-24T00:09:00+00:00",
      }))
      .mockResolvedValueOnce(createJsonResponse(completedExportSnapshot));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Export Audiobook");
    });

    await act(async () => {
      getButtonByText("Export Audiobook").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Include M4B");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.trim() === "Export")
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/book/7/export",
        expect.objectContaining({
          body: JSON.stringify({
            formats: ["mp3", "m4b"],
            include_only_approved: true,
          }),
          method: "POST",
        }),
      );
      expect(container.textContent).toContain("M4B (with chapter markers)");
      expect(container.textContent).toContain("The Test Chronicle.mp3");
    });

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent.includes("Copy Link"))
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(clipboardWriteMock).toHaveBeenCalledWith("http://localhost/api/book/7/export/download/mp3");
    });
  });

  test("marks a historical partial export as past export available instead of ready to download", async () => {
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
      createChapter({
        id: 702,
        number: 1,
        status: "pending",
        text_content: "Chapter one text.",
        title: "Chapter One",
        type: "chapter",
        word_count: 500,
      }),
      createChapter({
        id: 703,
        number: 2,
        status: "pending",
        text_content: "Chapter two text.",
        title: "Chapter Two",
        type: "chapter",
        word_count: 500,
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot({
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
      })))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot({
        completed_at: "2026-03-24T00:10:00+00:00",
        export_status: "completed",
        formats: {
          mp3: {
            completed_at: "2026-03-24T00:10:00+00:00",
            download_url: "/api/book/7/export/download/mp3",
            error_message: null,
            file_name: "The Test Chronicle.mp3",
            file_size_bytes: 487923048,
            status: "completed",
          },
        },
        qa_report: {
          book_id: 7,
          book_title: "The Test Chronicle",
          chapter_summary: [],
          chapters_approved: 1,
          chapters_flagged: 0,
          chapters_included: 1,
          chapters_warnings: 0,
          export_approved: true,
          export_date: "2026-03-24T00:10:00+00:00",
          notes: "Partial export completed.",
        },
      })))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Past export available");
      expect(container.textContent).toContain("Generate the remaining chapters and re-export for a complete audiobook.");
    });
  });

  test("does not show a stale partial-export warning when recovered exports have downloads but no qa report", async () => {
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
      createChapter({
        audio_path: "7-the-test-chronicle/chapters/01-chapter-one.wav",
        audio_file_size_bytes: 21000000,
        completed_at: "2026-03-24T00:02:00+00:00",
        duration_seconds: 500,
        id: 702,
        number: 1,
        status: "generated",
        text_content: "Chapter one text.",
        title: "Chapter One",
        type: "chapter",
        word_count: 500,
      }),
      createChapter({
        audio_path: "7-the-test-chronicle/chapters/02-chapter-two.wav",
        audio_file_size_bytes: 20000000,
        completed_at: "2026-03-24T00:03:00+00:00",
        duration_seconds: 480,
        id: 703,
        number: 2,
        status: "generated",
        text_content: "Chapter two text.",
        title: "Chapter Two",
        type: "chapter",
        word_count: 500,
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot({
        chapters: chapters.map((chapter) => ({
          audio_duration_seconds: chapter.duration_seconds,
          audio_file_size_bytes: chapter.audio_file_size_bytes,
          chapter_n: chapter.number,
          error_message: null,
          generated_at: chapter.completed_at,
          generation_seconds: 47.2,
          expected_total_seconds: 23.4,
          progress_seconds: null,
          started_at: "2026-03-24T00:00:00+00:00",
          status: "completed",
        })),
      })))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot({
        completed_at: "2026-03-24T00:10:00+00:00",
        current_stage: "Export completed",
        export_status: "completed",
        formats: {
          mp3: {
            completed_at: "2026-03-24T00:10:00+00:00",
            download_url: "/api/book/7/export/download/mp3",
            error_message: null,
            file_name: "The Test Chronicle.mp3",
            file_size_bytes: 487923048,
            status: "completed",
          },
          m4b: {
            completed_at: "2026-03-24T00:10:00+00:00",
            download_url: "/api/book/7/export/download/m4b",
            error_message: null,
            file_name: "The Test Chronicle.m4b",
            file_size_bytes: 341234056,
            status: "completed",
          },
        },
        qa_report: null,
      })))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Ready to download");
      expect(container.textContent).not.toContain("Past export available");
      expect(container.textContent).not.toContain("missing from the latest export");
    });
  });

  test("renders real export progress instead of a fake placeholder bar", async () => {
    const book = createBook({});
    const chapters = [createChapter({ status: "generated", audio_path: "7-the-test-chronicle/chapters/00-opening-credits.wav" })];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot({
        current_chapter_n: 3,
        current_format: "mp3",
        current_stage: "Encoding MP3 (chapter 3/10)",
        export_status: "processing",
        progress_percent: 45,
        started_at: "2026-03-24T00:09:00+00:00",
        total_chapters: 10,
      })))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("45%");
      expect(container.textContent).toContain("Encoding MP3 (chapter 3/10)");
    });
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
      .mockResolvedValueOnce(createJsonResponse(statusSnapshot))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()));

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

  test("runs book QA on demand and renders the Gate 3 overview", async () => {
    const book = createBook({ status: "generated" });
    const chapters = [
      createChapter({
        audio_path: "7-the-test-chronicle/chapters/01-chapter-one.wav",
        duration_seconds: 120,
        id: 701,
        number: 1,
        qa_status: "approved",
        status: "generated",
        title: "Chapter One",
        type: "chapter",
      }),
      createChapter({
        audio_path: "7-the-test-chronicle/chapters/02-chapter-two.wav",
        duration_seconds: 118,
        id: 702,
        number: 2,
        qa_status: "approved",
        status: "generated",
        text_content: "Chapter two body text.",
        title: "Chapter Two",
        type: "chapter",
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()))
      .mockResolvedValueOnce(createJsonResponse(createBookQualityReport()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceConsistencyChart()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Run Book QA");
    });

    await act(async () => {
      getButtonByText("Run Book QA").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Gate 3 Overview");
      expect(container.textContent).toContain("Grade A");
      expect(container.textContent).toContain("Ready for Export");
      expect(container.textContent).toContain("Voice Consistency Chart");
      expect(container.textContent).toContain("All chapters within ACX loudness range.");
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/book/7/qa/book-report");
    expect(fetchMock).toHaveBeenCalledWith("/api/book/7/qa/voice-consistency-chart");
  });

  test("runs book-level audio QA and renders the persisted deep audio report", async () => {
    const book = createBook({ status: "generated" });
    const chapters = [
      createChapter({
        audio_path: "/tmp/chapter-1.wav",
        duration_seconds: 120,
        id: 701,
        number: 1,
        qa_status: "approved",
        status: "generated",
        title: "Chapter One",
        type: "chapter",
      }),
      createChapter({
        audio_path: "/tmp/chapter-2.wav",
        duration_seconds: 118,
        id: 702,
        number: 2,
        qa_status: "approved",
        status: "generated",
        text_content: "Chapter two body text.",
        title: "Chapter Two",
        type: "chapter",
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()))
      .mockResolvedValueOnce(createJsonResponse(createDeepQaReport()));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Run Audio QA");
    });

    await act(async () => {
      getButtonByText("Run Audio QA").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Automated Audio QA Pipeline");
      expect(container.textContent).toContain("97.1 Avg");
      expect(container.textContent).toContain("Chapters Analyzed");
      expect(container.textContent).toContain("Chapter Scores");
      expect(container.textContent).toContain("TX 100.0");
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/books/7/deep-qa", { method: "POST" });
  });

  test("runs chapter deep QA and renders issue + diff details for the selected chapter", async () => {
    const book = createBook({ status: "generated" });
    const chapters = [
      createChapter({
        audio_path: "/tmp/chapter-1.wav",
        duration_seconds: 120,
        id: 701,
        number: 1,
        qa_status: "approved",
        status: "generated",
        title: "Chapter One",
        type: "chapter",
      }),
    ];

    fetchMock
      .mockResolvedValueOnce(createJsonResponse(book))
      .mockResolvedValueOnce(createJsonResponse(chapters))
      .mockResolvedValueOnce(createJsonResponse(createStatusSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createExportSnapshot()))
      .mockResolvedValueOnce(createJsonResponse(createVoiceListPayload()))
      .mockResolvedValueOnce(createJsonResponse({ ok: true }))
      .mockResolvedValueOnce(createJsonResponse(createDeepQaReport({ chapter_count: 1, chapters: [createDeepQaReport().chapters[0]] })));

    await renderBookDetail();

    await waitFor(() => {
      expect(container.textContent).toContain("Deep QA");
    });

    await act(async () => {
      getButtonByText("Deep QA").dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(container.textContent).toContain("Transcript Diff");
      expect(container.textContent).toContain("Transcript segment drift detected.");
      expect(container.textContent).toContain("1.50s - 2.20s");
      expect(container.textContent).toContain("Expected:");
      expect(container.textContent).toContain("chapter one");
      expect(container.textContent).toContain("chapter two");
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/books/7/chapters/701/deep-qa", { method: "POST" });
    expect(fetchMock).toHaveBeenCalledWith("/api/books/7/qa-report");
  });
});
