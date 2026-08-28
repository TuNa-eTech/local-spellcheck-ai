import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ invoke: vi.fn(), listen: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ invoke: mocks.invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: mocks.listen }));
vi.mock("@tauri-apps/api/app", () => ({ getVersion: vi.fn() }));
vi.mock("@tauri-apps/api/webview", () => ({ getCurrentWebview: vi.fn() }));

import { api } from "../src/api";

describe("desktop API", () => {
  beforeEach(() => {
    mocks.invoke.mockReset();
    mocks.listen.mockReset();
    mocks.listen.mockResolvedValue(() => undefined);
    mocks.invoke.mockResolvedValue({
      job_id: "job-1",
      status: "no_findings",
      output_path: null,
      finding_count: 0,
      counts: {},
    });
    Object.defineProperty(window, "__TAURI_INTERNALS__", { configurable: true, value: {} });
  });

  it("sends the fixed baseline and full-review options using camelCase", async () => {
    const ruleOptions = {
      technical: true,
      repeated_words: true,
      confusions: true,
      syllables: true,
      administrative_capitalization: false,
    };

    await api.startJob(
      "job-1",
      "C:\\Tài liệu\\nguồn.docx",
      "standard",
      "Giữ nguyên SoátVăn.\n\nDùng thuật ngữ khách hàng.",
      true,
      ruleOptions,
      [],
      true,
      true,
    );

    expect(mocks.invoke).toHaveBeenCalledWith("start_job", {
      request: {
        jobId: "job-1",
        sourcePath: "C:\\Tài liệu\\nguồn.docx",
        preset: "standard",
        customPrompt: "Giữ nguyên SoátVăn.\n\nDùng thuật ngữ khách hàng.",
        useModel: true,
        fullReview: true,
        includeRuleFindings: true,
        ruleOptions,
        ignoredWords: [],
      },
    });
  });

  it("bridges custom-rule CRUD without changing Unicode prompt text", async () => {
    const entry = {
      id: "0fb7daf2-3647-4e7d-b3fb-2dac4b6af240",
      prompt: 'Giữ nguyên “SoátVăn” và dấu nháy \'đơn\'.',
      created_at: "2026-08-25T01:00:00.000000Z",
      updated_at: "2026-08-25T01:00:00.000000Z",
    };
    mocks.invoke
      .mockResolvedValueOnce([entry])
      .mockResolvedValueOnce(entry)
      .mockResolvedValueOnce(true);

    await expect(api.customRuleList()).resolves.toEqual([entry]);
    await expect(api.customRuleUpsert(null, entry.prompt)).resolves.toEqual(entry);
    await expect(api.customRuleDelete(entry.id)).resolves.toBe(true);

    expect(mocks.invoke).toHaveBeenNthCalledWith(1, "custom_rule_list");
    expect(mocks.invoke).toHaveBeenNthCalledWith(2, "custom_rule_upsert", {
      id: null,
      prompt: entry.prompt,
    });
    expect(mocks.invoke).toHaveBeenNthCalledWith(3, "custom_rule_delete", { id: entry.id });
  });

  it("subscribes only to legal Tauri event names", async () => {
    await api.onProgress(() => undefined, "job-1");
    await api.onModelProgress(() => undefined);

    expect(mocks.listen.mock.calls.map(call => call[0])).toEqual([
      "job-progress",
      "model-progress",
    ]);
    for (const [eventName] of mocks.listen.mock.calls) {
      expect(eventName).toMatch(/^[A-Za-z0-9_:/-]+$/);
      expect(eventName).not.toContain(".");
    }
  });
});
