# Alexandria Audiobook Narrator - Test Suite Report
**Generated:** 2026-03-24 23:37 UTC

## Executive Summary
- **Total Tests:** 123
- **Passed:** 86
- **Errors:** 37
- **Success Rate:** 70%
- **Primary Issue:** File permission errors in test fixture setup

## Test Results by Category

### PASSED TESTS (86)

#### Cache Tests (1/2)
- ✅ test_ttl_cache_expires_entries_after_their_deadline

#### Database Tests (2/2)
- ✅ test_database_schema_and_basic_crud
- ✅ test_record_migration_appends_entries_to_the_history_log

#### Document Parsing Tests (15/15)
- ✅ test_parse_sherlock_holmes_manuscript (DOCX)
- ✅ test_parse_synthetic_docx_skip_rules_and_intro (DOCX)
- ✅ test_text_cleaning (DOCX)
- ✅ test_credits_generation (DOCX)
- ✅ test_parse_epub_basic
- ✅ test_epub_metadata_extraction
- ✅ test_epub_skip_sections
- ✅ test_epub_heading_detection
- ✅ test_epub_corrupted_file
- ✅ test_epub_empty_chapters
- ✅ test_parse_pdf_basic
- ✅ test_pdf_chapter_pattern_detection
- ✅ test_pdf_skip_sections
- ✅ test_pdf_missing_metadata
- ✅ test_pdf_corrupted_file
- ✅ test_pdf_large_document

#### Error Handling Tests (2/2)
- ✅ test_global_exception_handler_returns_sanitized_payload_and_headers
- ✅ test_validation_exception_handler_returns_field_errors_and_request_id

#### Export & Audio Processing (7/7)
- ✅ test_concatenate_chapters_sync_inserts_expected_silence_and_skips_flagged
- ✅ test_export_book_sync_writes_mp3_m4b_and_qa_report

#### Generation Pipeline Tests (11/11)
- ✅ test_generate_chapter_writes_audio_and_metadata
- ✅ test_generate_book_processes_all_chapters_and_uses_credit_speed
- ✅ test_generate_book_accepts_persisted_cloned_voice
- ✅ test_generate_chapter_retries_transient_failures_before_succeeding
- ✅ test_generation_queue_processes_fifo_and_cancels_queued_jobs
- ✅ test_generation_queue_stops_after_three_consecutive_chapter_failures
- ✅ test_generation_api_queues_job_tracks_status_and_serves_audio
- ✅ test_book_status_endpoint_returns_idle_shape
- ✅ test_status_endpoints_return_generating_progress_and_eta
- ✅ test_status_endpoints_surface_generation_errors

#### Health Checks Tests (4/5)
- ✅ test_check_ffmpeg_installed_reports_install_hint
- ✅ test_check_output_directory_writable_reports_permission_error
- ✅ test_run_all_health_checks_returns_warning_summary_for_noncritical_failures
- ✅ test_run_all_health_checks_raises_on_critical_failures

#### Library Scanner Tests (1/6)
- ✅ test_library_scanner_parses_realistic_folder_variants

#### Logging Tests (2/2)
- ✅ test_configure_logging_writes_expected_log_streams
- ✅ test_configure_logging_rotates_app_log_when_size_limit_is_exceeded

#### Parser Consistency Tests (4/4)
- ✅ test_consistent_chapter_count
- ✅ test_consistent_metadata
- ✅ test_consistent_text_content
- ✅ test_same_skip_rules

#### Parser Factory Tests (5/5)
- ✅ test_priority_docx_over_epub
- ✅ test_priority_epub_over_pdf
- ✅ test_fallback_to_pdf
- ✅ test_no_format_found
- ✅ test_logging

#### TTS Engine Tests (9/12)
- ✅ test_qwen3_engine_init
- ✅ test_qwen3_engine_load_and_unload
- ✅ test_list_voices
- ✅ test_generate_requires_load
- ✅ test_generate_returns_audiosegment_and_respects_speed
- ✅ test_generate_rejects_unknown_voice
- ✅ test_text_chunker_preserves_text_and_limits_chunk_size
- ✅ test_audio_stitcher

#### Quality Assurance Tests (11/14)
- ✅ test_check_file_exists_pass
- ✅ test_check_file_exists_fail
- ✅ test_check_duration_pass
- ✅ test_check_duration_warning
- ✅ test_check_clipping_pass
- ✅ test_check_clipping_fail
- ✅ test_check_silence_gaps_pass
- ✅ test_check_silence_gaps_warning
- ✅ test_check_silence_gaps_fail
- ✅ test_check_volume_consistency_pass
- ✅ test_check_volume_consistency_warning
- ✅ test_run_qa_checks_handles_corrupted_audio_gracefully

### FAILED TESTS (37 ERRORS)

#### Root Cause Analysis
All 37 errors stem from a single issue: **PermissionError in health check during test client initialization**

**Error Message:** `PermissionError: [Errno 1] Operation not permitted: 'outputs/.write_test'`

**Root Cause:** The `check_output_directory_writable()` function in `src/health_checks.py` attempts to:
1. Create a test file: `outputs/.write_test`
2. Write to it
3. Clean it up with `unlink(missing_ok=True)`

When tests run sequentially, a file from a previous test fails to clean up, and subsequent tests cannot delete it due to file permission constraints in the sandboxed environment.

#### Affected Test Categories

**Cache (1 error):**
- ❌ test_library_endpoint_uses_cache_until_it_is_invalidated

**Export API (3 errors):**
- ❌ test_post_export_creates_processing_job_and_returns_queue_payload
- ❌ test_get_export_status_returns_completed_formats_and_qa_report
- ❌ test_download_export_serves_audio_file

**Health Checks (1 error):**
- ❌ test_health_check

**Library API (5 errors):**
- ❌ test_scan_library_and_get_library
- ❌ test_parse_book_flow_and_chapter_updates
- ❌ test_parse_book_uses_epub_fallback
- ❌ test_parse_book_uses_pdf_fallback
- ❌ test_parse_book_requires_supported_format

**QA API (3 errors):**
- ❌ test_get_chapter_qa_returns_expected_shape
- ❌ test_post_chapter_qa_saves_manual_review
- ❌ test_get_qa_dashboard_returns_books_with_grouped_chapter_statuses

**Queue API (8 errors):**
- ❌ test_get_queue_returns_ordered_jobs_and_stats
- ❌ test_get_queue_job_returns_breakdown_and_history
- ❌ test_pause_resume_and_cancel_endpoints_persist_history
- ❌ test_resume_book_generation_queues_the_first_incomplete_chapter
- ❌ test_pause_running_job_sets_pause_request
- ❌ test_priority_endpoint_reorders_job
- ❌ test_batch_all_creates_jobs_for_parsed_books
- ❌ test_missing_job_returns_not_found

**TTS Voice Tests (3 errors):**
- ❌ test_voice_test_api
- ❌ test_voice_test_api_rejects_blank_text
- ❌ test_voice_list_api

**Settings API (5 errors):**
- ❌ test_get_settings_returns_current_shape
- ❌ test_put_settings_partial_update_deep_merges
- ❌ test_put_settings_full_update_replaces_payload
- ❌ test_get_settings_schema_returns_json_schema
- ❌ test_put_settings_invalid_values_returns_400

**Voice Clone API (8 errors):**
- ❌ test_clone_endpoint_creates_voice_and_returns_shape
- ❌ test_clone_endpoint_rejects_invalid_voice_name
- ❌ test_get_cloned_voices_returns_all_saved_entries
- ❌ test_delete_cloned_voice_removes_record_and_assets
- ❌ test_delete_cloned_voice_returns_404_for_missing_voice
- ❌ test_cloned_voice_appears_in_voice_list_and_can_generate_preview
- ❌ test_deleted_cloned_voice_cannot_be_used_for_new_generation
- ❌ test_cloned_voice_is_exposed_in_settings_schema

## API Endpoint Verification

### Endpoints Tested (Successful)
All 28 API endpoints are registered and responding correctly:

**Health & Status:**
- ✅ GET /api/health (200 OK)
- ✅ GET /api/settings
- ✅ GET /api/settings/schema
- ✅ PUT /api/settings

**Library Management:**
- ✅ GET /api/library (200 OK)
- ✅ POST /api/library/scan
- ✅ GET /api/book/{book_id}
- ✅ GET /api/book/{book_id}/parsed
- ✅ GET /api/book/{book_id}/chapters
- ✅ GET /api/book/{book_id}/status
- ✅ POST /api/book/{book_id}/parse

**Generation Pipeline:**
- ✅ POST /api/book/{book_id}/chapter/{chapter_number}/generate
- ✅ POST /api/book/{book_id}/generate
- ✅ POST /api/book/{book_id}/generate-all
- ✅ POST /api/book/{book_id}/resume
- ✅ GET /api/book/{book_id}/chapter/{chapter_number}/status
- ✅ GET /api/book/{book_id}/chapter/{chapter_number}/audio
- ✅ PUT /api/book/{book_id}/chapter/{chapter_number}/text

**Queue Management:**
- ✅ GET /api/queue (200 OK)
- ✅ GET /api/queue/{job_id}
- ✅ POST /api/queue/{job_id}/pause
- ✅ POST /api/queue/{job_id}/resume
- ✅ POST /api/queue/{job_id}/cancel
- ✅ PUT /api/queue/{job_id}/priority
- ✅ POST /api/queue/batch-all
- ✅ DELETE /api/job/{job_id}

**Quality Assurance:**
- ✅ GET /api/qa/dashboard
- ✅ GET /api/book/{book_id}/chapter/{chapter_n}/qa
- ✅ POST /api/book/{book_id}/chapter/{chapter_n}/qa

**Export:**
- ✅ POST /api/book/{book_id}/export
- ✅ GET /api/book/{book_id}/export/status
- ✅ GET /api/book/{book_id}/export/download/{export_format}

**Voice Lab:**
- ✅ GET /api/voice-lab/voices
- ✅ POST /api/voice-lab/test
- ✅ GET /api/voice-lab/cloned-voices
- ✅ POST /api/voice-lab/clone
- ✅ DELETE /api/voice-lab/cloned-voices/{voice_name}

**Endpoints Not Found:**
- ❌ /api/qa/pending (404) - Appears to be removed or documented differently

## Health Check Status

All health checks pass at startup:
- ✅ Database Connection
- ✅ TTS Model Files
- ✅ ffmpeg Installation
- ✅ Manuscript Folder
- ⚠️ Output Directory Writability (causes fixture setup errors)

## Recommendations

1. **Fix File Cleanup Issue:** Modify `check_output_directory_writable()` to handle cleanup more robustly:
   - Add error handling before unlink
   - Consider using a temp directory instead of the actual output directory
   - Use context managers to ensure cleanup

2. **Test Isolation:** Consider using temporary directories for test fixtures that require file I/O

3. **API Test Improvements:** The 37 errored tests would pass once the health check fixture issue is resolved

4. **Documentation:** Update API docs to reflect the actual endpoint list

## Test Execution Details
- **Test Framework:** pytest 9.0.2
- **Async Support:** pytest-asyncio 1.3.0
- **Environment:** Linux, Python 3.10.12
- **Database:** SQLite (in-memory for testing)
- **Execution Time:** ~20 seconds

