"use strict";

/* Offline mechanism test: execute current reader functions with controlled API
 * promises. This does not start a browser/server or make any network request. */
const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const names = ["app.js", "shell.js", "manage.js"];
const files = Object.fromEntries(names.map(name => [name, path.join(root, "research_workspace", "static", name)]));
const source = Object.fromEntries(names.map(name => [name, fs.readFileSync(files[name], "utf8")]));
const digest = value => crypto.createHash("sha256").update(value).digest("hex");
const snapshot = Object.fromEntries(names.map(name => [name, digest(source[name])]));
const output = path.join(root, "artifacts", "verification", `navigation-race-${new Date().toISOString().replace(/[:.]/g, "-")}-${crypto.randomBytes(4).toString("hex")}`);
fs.mkdirSync(output, { recursive: true });
const report = {
  started_at: new Date().toISOString(), output, mode: "offline_mechanism_test",
  synthetic_fixture: true, source_sha256: snapshot, checks: [], network_requests: 0,
  limitations: [
    "Executes functions extracted from current source in Node VM with synthetic document data and deferred API stubs.",
    "Checks shell/manage invalidation call sites in current source; does not claim browser DOM, rendering, or HTTP integration coverage.",
    "No server, external service, model call, real account, or private document is used."
  ]
};
const git = args => {
  const result = spawnSync("git", args, { cwd: root, encoding: "utf8", windowsHide: true });
  return result.status === 0 ? result.stdout.trim() : null;
};
report.git_head = git(["rev-parse", "HEAD"]);
report.git_status = git(["status", "--short"]);

function between(text, start, end) {
  const begin = text.indexOf(start);
  assert.notEqual(begin, -1, `Source marker is present: ${start}`);
  const finish = text.indexOf(end, begin + start.length);
  assert.notEqual(finish, -1, `Source marker is present after start: ${end}`);
  return text.slice(begin, finish);
}

function fixture(ids = ["B"]) {
  const state = {
    documents: ids.map(id => ({ id, page_count: 10, available_run: { id: `latest-${id}` } })),
    selectedId: ids[0] || null, selectedRunId: ids[0] ? `latest-${ids[0]}` : null,
    pinnedRun: false, page: 1, highlight: null, selectedHit: null, pageHasText: null
  };
  const pending = new Map(), calls = [], events = [], loads = [], views = [];
  const api = request => {
    calls.push(request);
    assert.match(request, /^\/api\/documents\/[^/?]+$/, "Only an expected local document lookup is requested.");
    const id = decodeURIComponent(request.split("/").at(-1));
    assert.ok(!pending.has(id), `Only one outstanding lookup for ${id} in this fixture.`);
    return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); });
  };
  const context = vm.createContext({
    state, api, ui: { searchScope: { value: "all" }, pageNumber: { value: "1" } },
    currentDocument: () => state.documents.find(doc => doc.id === state.selectedId),
    readableRun: doc => doc.available_run || doc.current_run,
    normalizeBox: box => Array.isArray(box) ? Array.from(box) : null,
    renderLibrary() {}, renderReaderHeading() {}, renderHitSelection() {}, clearSearch() {},
    refreshDocuments() { throw new Error("Unexpected document refresh in navigation fixture."); },
    loadPage: () => loads.push({ id: state.selectedId, page: state.page, run_id: state.selectedRunId }),
    setView: name => views.push(name),
    window: { dispatchEvent: event => events.push(event.type) },
    CustomEvent: class CustomEvent { constructor(type) { this.type = type; } }
  });
  const select = between(source["app.js"], "  function selectDocument(id, options = {}) {", "\n  function normalizeBox(");
  const turnPage = between(source["app.js"], "  function goToPage(value) {", "\n  function setUploadBusy(");
  const bridge = between(source["app.js"], "  window.ResearchWorkspace = {", "\n  initialize();");
  vm.runInContext(`let navigationRequest = 0;\n${select}\n${turnPage}\n${bridge}\nglobalThis.fixtureControls = { selectDocument, goToPage };`, context, { filename: "current-app-navigation-extract.js" });
  const value = context.window.ResearchWorkspace;
  assert.equal(typeof value.onEvidence, "function");
  assert.equal(typeof value.invalidateEvidenceNavigation, "function", "Current bridge exposes explicit navigation invalidation.");
  return {
    state, calls, events, loads, views, bridge: value, controls: context.fixtureControls,
    resolve(id) {
      const request = pending.get(id);
      assert.ok(request, `Document ${id} has an actual pending API stub call.`);
      pending.delete(id);
      request.resolve({ document: { id, page_count: 10, available_run: { id: `latest-${id}` } } });
    }
  };
}

const evidence = (id, page = 2) => ({ document_id: id, run_id: `evidence-run-${id}`, page, bbox: [0.1, 0.2, 0.4, 0.5], block_id: `block-${id}` });
const ids = value => value.state.documents.map(doc => doc.id);
const check = async (name, action) => {
  const detail = await action();
  report.checks.push({ name, passed: true, ...(detail || {}) });
  console.log(`PASS ${name}`);
};

(async () => {
  try {
    await check("Late missing A cannot override a newer evidence navigation to existing B", async () => {
      const f = fixture();
      const first = f.bridge.onEvidence(evidence("A-archived"));
      await f.bridge.onEvidence(evidence("B", 4));
      f.resolve("A-archived"); await first;
      assert.deepEqual(ids(f), ["B"], "Stale result must not append A to reader state.");
      assert.equal(f.state.selectedId, "B"); assert.equal(f.state.page, 4);
      assert.equal(f.state.selectedRunId, "evidence-run-B"); assert.equal(f.state.pinnedRun, true);
      assert.deepEqual(f.state.highlight, evidence("B").bbox);
      assert.deepEqual(f.events, ["research:reader"]);
      assert.deepEqual(f.loads.map(item => item.id), ["B"]);
      return { selected_document: f.state.selectedId, stored_documents: ids(f) };
    });

    await check("Explicit lifecycle invalidation prevents late A navigation and state insertion", async () => {
      const f = fixture();
      const first = f.bridge.onEvidence(evidence("A-archived"));
      f.bridge.invalidateEvidenceNavigation();
      f.resolve("A-archived"); await first;
      assert.deepEqual(ids(f), ["B"]); assert.equal(f.state.selectedId, "B");
      assert.equal(f.state.page, 1); assert.equal(f.state.selectedRunId, "latest-B");
      assert.deepEqual(f.events, []); assert.deepEqual(f.loads, []); assert.deepEqual(f.views, []);
    });

    await check("Two delayed document lookups accept only the latest evidence navigation", async () => {
      const f = fixture(["C"]);
      const first = f.bridge.onEvidence(evidence("A-archived"));
      const second = f.bridge.onEvidence(evidence("B", 5));
      f.resolve("B"); await second;
      f.resolve("A-archived"); await first;
      assert.deepEqual(ids(f), ["C", "B"]); assert.equal(f.state.selectedId, "B");
      assert.equal(f.state.page, 5); assert.deepEqual(f.events, ["research:reader"]);
      assert.deepEqual(f.loads.map(item => item.id), ["B"]);
    });

    await check("Actual reader page navigation invalidates a pending evidence lookup", async () => {
      const f = fixture();
      const first = f.bridge.onEvidence(evidence("A-archived"));
      f.controls.goToPage(3);
      f.resolve("A-archived"); await first;
      assert.deepEqual(ids(f), ["B"]); assert.equal(f.state.selectedId, "B");
      assert.equal(f.state.page, 3); assert.deepEqual(f.events, []);
      assert.deepEqual(f.loads, [{ id: "B", page: 3, run_id: "latest-B" }]);
    });

    await check("Actual document selection invalidates a pending evidence lookup", async () => {
      const f = fixture(["B", "C"]);
      const first = f.bridge.onEvidence(evidence("A-archived"));
      f.controls.selectDocument("C", { page: 6 });
      f.resolve("A-archived"); await first;
      assert.deepEqual(ids(f), ["B", "C"]); assert.equal(f.state.selectedId, "C");
      assert.equal(f.state.page, 6); assert.deepEqual(f.events, []);
      assert.deepEqual(f.loads.map(item => item.id), ["C"]);
    });

    await check("Current missing-document evidence lookup preserves its run, page, block and bbox", async () => {
      const f = fixture();
      const first = f.bridge.onEvidence(evidence("A-archived", 7));
      f.resolve("A-archived"); await first;
      assert.deepEqual(ids(f), ["B", "A-archived"]); assert.equal(f.state.selectedId, "A-archived");
      assert.equal(f.state.selectedRunId, "evidence-run-A-archived"); assert.equal(f.state.pinnedRun, true);
      assert.equal(f.state.page, 7); assert.equal(f.state.selectedHit, "block-A-archived");
      assert.deepEqual(f.state.highlight, evidence("A-archived").bbox);
      assert.deepEqual(f.events, ["research:reader"]);
      assert.deepEqual(f.loads, [{ id: "A-archived", page: 7, run_id: "evidence-run-A-archived" }]);
    });

    await check("Latest source connects module, tab, document and destruction lifecycle invalidation", () => {
      const hooks = {
        "shell.activate": between(source["shell.js"], "  async function activate(", "\n  document.querySelectorAll('[data-module]')"),
        "manage.switchTab": between(source["manage.js"], "    function switchTab(", "\n    function clearExtensions()"),
        "manage.loadDocument": between(source["manage.js"], "    async function loadDocument(id) {", "\n    function renderMetadata("),
        "manage.destroy": between(source["manage.js"], "      destroy() {", "\n    };")
      };
      for (const [name, body] of Object.entries(hooks)) {
        assert.match(body, /\binvalidateEvidenceNavigation\s*(?:\?\.)?\s*\(/, `${name} invokes navigation invalidation.`);
        const awaitAt = body.indexOf("await ");
        assert.ok(awaitAt === -1 || body.indexOf("invalidateEvidenceNavigation") < awaitAt, `${name} invalidates before an async wait.`);
      }
      return { inspected_call_sites: Object.keys(hooks), verification: "source-call-site-check; not a DOM test" };
    });

    await check("Source files remained unchanged during the mechanism run", () => {
      for (const name of names) assert.equal(digest(fs.readFileSync(files[name], "utf8")), snapshot[name], `${name} is unchanged.`);
    });
    report.passed = true;
  } catch (error) {
    report.passed = false;
    report.failure = { message: error.message, stack: error.stack };
    process.exitCode = 1;
  } finally {
    report.finished_at = new Date().toISOString();
    fs.writeFileSync(path.join(output, "report.json"), JSON.stringify(report, null, 2), { flag: "wx" });
    console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.join(output, "report.json"), failure: report.failure?.message }, null, 2));
  }
})();
