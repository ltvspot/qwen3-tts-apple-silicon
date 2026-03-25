import React, { act } from "react";
import ReactDOM from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import ProductionOverseer from "./ProductionOverseer";

function createJsonResponse(payload) {
  return {
    json: async () => payload,
    ok: true,
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

function createTrendBook(overrides = {}) {
  return {
    avg_lufs: -20.1,
    avg_wer: 0.06,
    book_id: 7,
    chunks_regenerated: 1,
    completed_at: "2026-03-25T12:00:00Z",
    gate1_pass_rate: 98.2,
    gate2_avg_grade: 3.8,
    gate3_overall_grade: "A",
    generation_rtf: 1.42,
    issues_found: 2,
    title: "The Test Chronicle",
    total_chapters: 9,
    ...overrides,
  };
}

function createBookReport(overrides = {}) {
  return {
    book_id: 7,
    title: "The Test Chronicle",
    total_chapters: 9,
    manuscript_validation: {
      difficulty_score: 3.2,
      issue_summary: { errors: 0, info: 2, warnings: 1 },
      issues: [],
      ready_for_generation: true,
      title: "The Test Chronicle",
      total_chapters: 9,
      total_words: 47094,
    },
    gate1_summary: {
      avg_wer: 0.05,
      chunks_pass_first_attempt: 23,
      chunks_regenerated: 1,
      failed_chunks: 0,
      gate1_pass_rate: 95.8,
      issue_chunks: 1,
      total_chunks: 24,
      warning_chunks: 1,
    },
    gate2_summary: {
      average_grade: 3.8,
      chapters: [],
      chapters_grade_a: 8,
      chapters_grade_b: 1,
      chapters_grade_c: 0,
      chapters_grade_f: 0,
      chapters_pending_manual: 0,
    },
    gate3_report: {
      overall_grade: "A",
      ready_for_export: true,
      cross_chapter_checks: {
        acx_compliance: { message: "All chapters satisfy ACX/Audible requirements.", status: "pass", violations: [] },
      },
      recommendations: ["All chapters within ACX loudness range."],
    },
    flagged_chapters: [],
    pronunciation_issues: [
      {
        chapter_n: 3,
        chapter_title: "The Party",
        context: "This word is known to cause pronunciation issues with Qwen3-TTS",
        pronunciation_guide: "hy-PER-bo-lee",
        word: "hyperbole",
      },
    ],
    export_verification: {
      blockers: [],
      checks: [
        { detail: "9/9 chapters", name: "all_chapters_generated", passed: true },
        { detail: "All chapters grade B or better", name: "gate2_minimum_grade", passed: true },
        { detail: "Book grade: A", name: "gate3_passed", passed: true },
        { detail: "Loudness normalized, edges trimmed", name: "mastering_complete", passed: true },
        { detail: "All chapters satisfy ACX/Audible requirements.", name: "acx_compliance", passed: true },
        { detail: "Title, author, narrator set", name: "metadata_complete", passed: true },
        { detail: "9 markers, all sequential", name: "chapter_markers_valid", passed: true },
      ],
      ready_for_export: true,
      recommendations: ["All chapters within ACX loudness range."],
      title: "The Test Chronicle",
    },
    quality_snapshot: createTrendBook(),
    ...overrides,
  };
}

describe("Production Overseer page", () => {
  let container;
  let fetchMock;
  let root;

  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = ReactDOM.createRoot(container);

    fetchMock = jest.fn((url) => {
      if (url === "/api/queue?limit=10&offset=0") {
        return Promise.resolve(createJsonResponse({
          jobs: [
            {
              book_title: "The Test Chronicle",
              current_chapter_n: 4,
              eta_seconds: 1260,
              progress_percent: 42,
              started_at: "2026-03-25T12:00:00Z",
              status: "generating",
            },
          ],
          queue_stats: {
            total_books_in_queue: 3,
            total_chapters: 44,
          },
        }));
      }

      if (url === "/api/batch/progress") {
        return Promise.resolve(createJsonResponse({
          current_book_title: "The Test Chronicle",
          percent_complete: 48,
          started_at: "2026-03-25T11:00:00Z",
          status: "running",
        }));
      }

      if (url === "/api/monitoring/resources") {
        return Promise.resolve(createJsonResponse({
          cpu_percent: 22.3,
          disk_free_gb: 420,
          disk_total_gb: 1000,
          disk_used_percent: 58,
          memory_total_mb: 32000,
          memory_used_mb: 12600,
          memory_used_percent: 39.4,
        }));
      }

      if (url === "/api/monitoring/model") {
        return Promise.resolve(createJsonResponse({
          last_canary_deviation_percent: 2.4,
          last_canary_status: "ok",
          last_reload_at: 1760000000,
          reload_count: 2,
          uptime_seconds: 7200,
        }));
      }

      if (url === "/api/overseer/quality-trend?last_n=20") {
        return Promise.resolve(createJsonResponse({
          alerts: [],
          avg_chunks_regenerated: 1.4,
          avg_gate1_pass_rate: 97.2,
          avg_gate2_grade: 3.4,
          avg_generation_rtf: 1.38,
          books_analyzed: 2,
          gate3_grade_distribution: { A: 1, B: 1, C: 0, F: 0 },
          recent_books: [
            createTrendBook(),
            createTrendBook({
              book_id: 8,
              completed_at: "2026-03-25T10:00:00Z",
              gate2_avg_grade: 3.1,
              gate3_overall_grade: "B",
              issues_found: 5,
              title: "Second Chronicle",
            }),
          ],
          trend: "stable",
          trend_points: [
            { book_id: 8, chunks_regenerated: 2, completed_at: "2026-03-25T10:00:00Z", gate1_pass_rate: 96.1, gate2_avg_grade: 3.1, title: "Second Chronicle" },
            { book_id: 7, chunks_regenerated: 1, completed_at: "2026-03-25T12:00:00Z", gate1_pass_rate: 98.2, gate2_avg_grade: 3.8, title: "The Test Chronicle" },
          ],
        }));
      }

      if (url === "/api/overseer/flagged-items?limit=50") {
        return Promise.resolve(createJsonResponse({
          items: [
            {
              actions: ["Regenerate", "View Details"],
              book_id: 8,
              book_title: "Second Chronicle",
              chapter_n: 6,
              chapter_title: "Storm Cellar",
              qa_grade: "C",
              reason: "Gate 2 grade C",
            },
          ],
        }));
      }

      if (url === "/api/overseer/book/7/report") {
        return Promise.resolve(createJsonResponse(createBookReport()));
      }

      if (url === "/api/overseer/book/8/report") {
        return Promise.resolve(createJsonResponse(createBookReport({
          book_id: 8,
          title: "Second Chronicle",
          gate3_report: {
            overall_grade: "B",
            ready_for_export: false,
            cross_chapter_checks: {
              acx_compliance: { message: "Chapter 7 peak exceeds -3 dB.", status: "fail", violations: [{ chapter_n: 7 }] },
            },
            recommendations: ["Consider regenerating chapter 6."],
          },
          export_verification: {
            blockers: ["Chapter 7 peak exceeds -3 dB."],
            checks: [
              { detail: "12/12 chapters", name: "all_chapters_generated", passed: true },
              { detail: "11/12 chapters grade B or better", name: "gate2_minimum_grade", passed: false },
            ],
            ready_for_export: false,
            recommendations: ["Consider regenerating chapter 6."],
            title: "Second Chronicle",
          },
          pronunciation_issues: [],
        })));
      }

      throw new Error(`Unexpected fetch: ${url}`);
    });
    global.fetch = fetchMock;
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });

    delete global.fetch;
    container.remove();
  });

  async function renderPage() {
    await act(async () => {
      root.render(
        <MemoryRouter>
          <ProductionOverseer />
        </MemoryRouter>,
      );
    });
  }

  test("renders the production overview, scoreboard, flagged items, and selected book report", async () => {
    await renderPage();

    await waitFor(() => {
      expect(container.textContent).toContain("Production Overseer");
      expect(container.textContent).toContain("Active Production Overview");
      expect(container.textContent).toContain("Quality Scoreboard");
      expect(container.textContent).toContain("Flagged Items");
      expect(container.textContent).toContain("Export Readiness");
      expect(container.textContent).toContain("The Test Chronicle");
      expect(container.textContent).toContain("Storm Cellar");
      expect(container.textContent).toContain("All chapters satisfy ACX/Audible requirements.");
    });

    expect(fetchMock).toHaveBeenCalledWith("/api/overseer/book/7/report");
  });

  test("loads a new overseer report when a recent book row is selected", async () => {
    await renderPage();

    await waitFor(() => {
      expect(container.textContent).toContain("Second Chronicle");
    });

    await act(async () => {
      container.querySelectorAll("tbody tr")[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/overseer/book/8/report");
      expect(container.textContent).toContain("Chapter 7 peak exceeds -3 dB.");
    });
  });

  test("shows actionable empty states when no overseer data exists yet", async () => {
    fetchMock.mockImplementation((url) => {
      if (url === "/api/queue?limit=10&offset=0") {
        return Promise.resolve(createJsonResponse({ jobs: [], queue_stats: { total_books_in_queue: 0, total_chapters: 0 } }));
      }
      if (url === "/api/batch/progress") {
        return Promise.resolve(createJsonResponse(null));
      }
      if (url === "/api/monitoring/resources") {
        return Promise.resolve(createJsonResponse(null));
      }
      if (url === "/api/monitoring/model") {
        return Promise.resolve(createJsonResponse(null));
      }
      if (url === "/api/overseer/quality-trend?last_n=20") {
        return Promise.resolve(createJsonResponse({
          alerts: [],
          avg_chunks_regenerated: 0,
          avg_gate1_pass_rate: 0,
          avg_gate2_grade: 0,
          avg_generation_rtf: 0,
          books_analyzed: 0,
          gate3_grade_distribution: { A: 0, B: 0, C: 0, F: 0 },
          recent_books: [],
          trend: "stable",
          trend_points: [],
        }));
      }
      if (url === "/api/overseer/flagged-items?limit=50") {
        return Promise.resolve(createJsonResponse({ items: [] }));
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });

    await renderPage();

    await waitFor(() => {
      expect(container.textContent).toContain("No completed quality snapshots yet.");
      expect(container.textContent).toContain("No overseer report available yet.");
      expect(container.textContent).toContain("Open Queue");
      expect(container.textContent).toContain("Open Library");
    });
  });
});
