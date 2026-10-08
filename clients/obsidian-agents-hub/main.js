"use strict";

// Agents Hub for Obsidian.
//
// One file on purpose: Obsidian loads only main.js, and the hub serves this
// folder as a zip, so there is no build step between the source and what runs.
// Sections: constants, pure helpers (exported for tests), hub client, modals,
// chat view, settings tab, plugin.

const obsidian = require("obsidian");

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const VIEW_TYPE = "agents-hub-chat";
const HISTORY_LIMIT = 20;
const CONTEXT_LIMIT = 60000;
const AGENT_OWNER = "agents-hub";

const DEFAULT_SETTINGS = {
  hubUrl: "http://localhost:8000",
  apiKey: "",
  workspace: "",
  defaultAgent: "",
  insertMode: "cursor",
};

// ---------------------------------------------------------------------------
// Pure helpers. No Obsidian objects in here, so node can test them.
// ---------------------------------------------------------------------------

/** Trim, default to http:// when no scheme is typed, drop trailing slashes and a pasted /v1 suffix. */
function normalizeBaseUrl(raw) {
  let url = String(raw == null ? "" : raw).trim();
  if (!url) return "";
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) url = "http://" + url;
  url = url.replace(/\/+$/, "");
  // People paste the OpenAI style base URL; the plugin adds /v1 itself.
  url = url.replace(/\/v1$/i, "");
  return url.replace(/\/+$/, "");
}

/** No Authorization header without a key: a hub in single mode needs none. */
function buildHeaders(settings) {
  const headers = { "Content-Type": "application/json" };
  const key = String(settings.apiKey || "").trim();
  if (key) headers.Authorization = "Bearer " + key;
  const workspace = String(settings.workspace || "").trim();
  if (workspace) headers["X-Agents-Hub-Workspace"] = workspace;
  return headers;
}

/** The models list also holds plain provider models; only agents are useful here. */
function parseAgents(body) {
  const data = body && Array.isArray(body.data) ? body.data : [];
  const agents = [];
  for (const entry of data) {
    if (!entry || typeof entry !== "object" || entry.owned_by !== AGENT_OWNER) continue;
    let id = entry.agent_id;
    if (!id && typeof entry.id === "string") id = entry.id.replace(/^agent:/, "");
    if (!id) continue;
    agents.push({
      id: String(id),
      name: String(entry.name || id),
      description: String(entry.description || ""),
    });
  }
  return agents;
}

/**
 * Keep the last `limit` messages. A history that starts with an answer
 * confuses some providers, so the window is moved forward to a user turn.
 */
function trimHistory(messages, limit) {
  const max = limit || HISTORY_LIMIT;
  let out = messages.slice(-max);
  while (out.length > 1 && out[0].role !== "user") out = out.slice(1);
  return out;
}

function buildContextMessage(title, path, content) {
  let text = String(content == null ? "" : content);
  if (text.length > CONTEXT_LIMIT) text = text.slice(0, CONTEXT_LIMIT) + "\n\n(truncated)";
  return {
    role: "system",
    content: `Context from the Obsidian note "${title}" (path ${path}):\n\n${text}`,
  };
}

/** Context messages go first, then the trimmed conversation, which must end on the user's turn. */
function buildRequestBody(agentId, history, contextMessages, limit) {
  return {
    model: "agent:" + agentId,
    messages: [...(contextMessages || []), ...trimHistory(history, limit)],
  };
}

/** The hub answers errors as {error:{message}} or, for auth failures, {detail}. */
function extractError(status, json, text) {
  if (json && typeof json === "object") {
    if (json.error) {
      if (typeof json.error === "string") return json.error;
      if (json.error.message) return String(json.error.message);
    }
    if (typeof json.detail === "string") return json.detail;
    if (Array.isArray(json.detail) && json.detail.length) {
      const first = json.detail[0];
      return String((first && (first.msg || first.message)) || JSON.stringify(first));
    }
    if (json.message) return String(json.message);
  }
  const snippet = String(text || "").trim().slice(0, 200);
  return snippet ? `Hub returned ${status}: ${snippet}` : `Hub returned ${status}`;
}

function extractAnswer(json) {
  const choice = json && Array.isArray(json.choices) ? json.choices[0] : null;
  const content = choice && choice.message ? choice.message.content : "";
  return typeof content === "string" ? content : "";
}

function extractRunId(json) {
  return (json && json.agents_hub && json.agents_hub.run_id) || "";
}

/** First words of the question as a file name, without characters Obsidian forbids. */
function noteNameFromQuestion(question) {
  const cleaned = String(question || "")
    .replace(/[\\/:*?"<>|#^[\]]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return "Agents Hub answer";
  return cleaned.split(" ").slice(0, 8).join(" ");
}

// ---------------------------------------------------------------------------
// Hub client
// ---------------------------------------------------------------------------

class HubError extends Error {}

class HubClient {
  constructor(getSettings) {
    this.getSettings = getSettings;
  }

  /** Throws HubError with a message fit for a Notice when the settings are incomplete. */
  _config() {
    const settings = this.getSettings();
    const base = normalizeBaseUrl(settings.hubUrl);
    if (!base) {
      throw new HubError("Agents Hub is not configured. Open the plugin settings and set the hub URL.");
    }
    return { base, headers: buildHeaders(settings) };
  }

  async _request(method, path, body) {
    const { base, headers } = this._config();
    let res;
    try {
      // throw:false so a 4xx or 5xx comes back as data and its body can be read.
      // No timeout: an agent that runs tools can legitimately take minutes.
      res = await obsidian.requestUrl({
        url: base + path,
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
        throw: false,
      });
    } catch (err) {
      throw new HubError(`Could not reach the hub at ${base}: ${err && err.message ? err.message : err}`);
    }
    let json = null;
    try {
      json = res.json;
    } catch (_) {
      json = null;
    }
    if (res.status < 200 || res.status >= 300) {
      throw new HubError(extractError(res.status, json, res.text));
    }
    return json;
  }

  async listAgents() {
    return parseAgents(await this._request("GET", "/v1/models"));
  }

  async chat(agentId, history, contextMessages) {
    const json = await this._request("POST", "/v1/chat/completions", buildRequestBody(agentId, history, contextMessages));
    return { answer: extractAnswer(json), runId: extractRunId(json) };
  }
}

// ---------------------------------------------------------------------------
// Note helpers (these touch the workspace, so they are not in the pure set)
// ---------------------------------------------------------------------------

async function noteContext(app, file) {
  const content = await app.vault.cachedRead(file);
  return buildContextMessage(file.basename, file.path, content);
}

function selectionContext(file, selection) {
  const title = file ? file.basename : "selection";
  const path = file ? file.path : "";
  const msg = buildContextMessage(title, path, selection);
  msg.content = msg.content.replace("Context from the Obsidian note", "Selected text from the Obsidian note");
  return msg;
}

async function createNote(app, name, content) {
  let path = name + ".md";
  let n = 2;
  while (app.vault.getAbstractFileByPath(path)) path = `${name} ${n++}.md`;
  return app.vault.create(path, content);
}

function insertText(app, plugin, text) {
  const view = app.workspace.getActiveViewOfType(obsidian.MarkdownView) || plugin.lastMarkdownView;
  if (!view || !view.editor) {
    new obsidian.Notice("Open a note first, then insert the answer.");
    return false;
  }
  const editor = view.editor;
  if (plugin.settings.insertMode === "append") {
    const last = editor.lastLine();
    const end = { line: last, ch: editor.getLine(last).length };
    editor.replaceRange("\n\n" + text + "\n", end);
  } else {
    editor.replaceRange(text, editor.getCursor());
  }
  return true;
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

/** Asks for an instruction, shows the agent's rewrite, and applies it only after a confirm. */
class RewriteModal extends obsidian.Modal {
  constructor(app, plugin, editor, selection, file) {
    super(app);
    this.plugin = plugin;
    this.editor = editor;
    this.selection = selection;
    this.file = file;
    this.answer = "";
    // Where the selection was when asked: the person may click elsewhere while the agent works.
    this.from = editor.getCursor("from");
    this.to = editor.getCursor("to");
  }

  onOpen() {
    this.titleEl.setText("Rewrite selection");
    this.renderAsk();
  }

  renderAsk() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("p", { text: "What should the agent do with the selected text?", cls: "agents-hub-muted" });
    const input = contentEl.createEl("textarea", { cls: "agents-hub-modal-input" });
    input.rows = 4;
    input.placeholder = "For example: make it shorter and friendlier";
    const row = contentEl.createDiv({ cls: "agents-hub-modal-buttons" });
    const go = row.createEl("button", { text: "Rewrite", cls: "mod-cta" });
    const cancel = row.createEl("button", { text: "Cancel" });
    cancel.addEventListener("click", () => this.close());
    go.addEventListener("click", async () => {
      const instruction = input.value.trim();
      if (!instruction) return;
      go.disabled = true;
      go.setText("Thinking...");
      try {
        const agent = this.plugin.settings.defaultAgent;
        if (!agent) throw new HubError("Choose a default agent in the plugin settings first.");
        const history = [
          { role: "user", content: `${instruction}\n\nReply with only the rewritten text.\n\n${this.selection}` },
        ];
        const res = await this.plugin.hub.chat(agent, history, []);
        this.answer = res.answer;
        this.renderConfirm();
      } catch (err) {
        new obsidian.Notice(err.message);
        go.disabled = false;
        go.setText("Rewrite");
      }
    });
    input.focus();
  }

  renderConfirm() {
    const { contentEl } = this;
    contentEl.empty();
    contentEl.createEl("p", { text: "Replace the selection with this text?", cls: "agents-hub-muted" });
    contentEl.createEl("pre", { text: this.answer, cls: "agents-hub-modal-preview" });
    const row = contentEl.createDiv({ cls: "agents-hub-modal-buttons" });
    const yes = row.createEl("button", { text: "Replace", cls: "mod-cta" });
    const no = row.createEl("button", { text: "Keep original" });
    no.addEventListener("click", () => this.close());
    yes.addEventListener("click", () => {
      // The range stored when asking, not the current selection, is what the answer rewrites.
      this.editor.replaceRange(this.answer, this.from, this.to);
      this.close();
    });
  }

  onClose() {
    this.contentEl.empty();
  }
}

// ---------------------------------------------------------------------------
// Chat view
// ---------------------------------------------------------------------------

class ChatView extends obsidian.ItemView {
  constructor(leaf, plugin) {
    super(leaf);
    this.plugin = plugin;
    this.history = []; // {role, content, runId?, question?}
    this.attached = null; // context message pinned by a command
    this.agents = [];
    this.agentId = "";
    this.busy = false;
  }

  getViewType() {
    return VIEW_TYPE;
  }

  getDisplayText() {
    return "Agents Hub";
  }

  getIcon() {
    return "bot";
  }

  async onOpen() {
    const root = this.contentEl;
    root.empty();
    root.addClass("agents-hub-view");

    const top = root.createDiv({ cls: "agents-hub-top" });
    this.agentSelect = top.createEl("select", { cls: "dropdown agents-hub-agent" });
    this.agentSelect.addEventListener("change", () => {
      this.agentId = this.agentSelect.value;
    });
    const fresh = top.createEl("button", { text: "New conversation" });
    fresh.addEventListener("click", () => this.reset());

    this.messagesEl = root.createDiv({ cls: "agents-hub-messages" });
    this.attachedEl = root.createDiv({ cls: "agents-hub-attached" });

    const includeRow = root.createDiv({ cls: "agents-hub-include" });
    this.includeBox = includeRow.createEl("input", { type: "checkbox" });
    this.includeBox.id = "agents-hub-include-note";
    includeRow.createEl("label", { text: "Include current note", attr: { for: this.includeBox.id } });

    const form = root.createDiv({ cls: "agents-hub-form" });
    this.input = form.createEl("textarea", { cls: "agents-hub-input" });
    this.input.rows = 3;
    this.input.placeholder = "Ask an agent. Ctrl or Cmd plus Enter sends.";
    this.input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        this.send();
      }
    });
    this.sendBtn = form.createEl("button", { text: "Send", cls: "mod-cta" });
    this.sendBtn.addEventListener("click", () => this.send());

    this.renderAttached();
    this.renderEmpty();
    await this.loadAgents();
  }

  async loadAgents() {
    this.agentSelect.empty();
    try {
      this.agents = await this.plugin.hub.listAgents();
    } catch (err) {
      this.agents = [];
      this.renderEmpty(err.message);
    }
    if (!this.agents.length) {
      this.agentSelect.createEl("option", { text: "No agents", value: "" });
      this.agentId = this.plugin.settings.defaultAgent || "";
      return;
    }
    for (const a of this.agents) {
      this.agentSelect.createEl("option", { text: a.name, value: a.id });
    }
    const wanted = this.agentId || this.plugin.settings.defaultAgent;
    this.agentId = this.agents.some((a) => a.id === wanted) ? wanted : this.agents[0].id;
    this.agentSelect.value = this.agentId;
  }

  /** Pin a note or selection as context for the next questions. */
  attach(contextMessage, label) {
    this.attached = contextMessage ? { message: contextMessage, label } : null;
    this.renderAttached();
  }

  renderAttached() {
    if (!this.attachedEl) return;
    this.attachedEl.empty();
    if (!this.attached) return;
    this.attachedEl.createSpan({ text: "Attached: " + this.attached.label, cls: "agents-hub-muted" });
    const drop = this.attachedEl.createEl("button", { text: "Remove", cls: "agents-hub-link" });
    drop.addEventListener("click", () => this.attach(null));
  }

  reset() {
    this.history = [];
    this.renderEmpty();
  }

  renderEmpty(error) {
    this.messagesEl.empty();
    if (error) {
      this.messagesEl.createDiv({ text: error, cls: "agents-hub-error" });
    } else if (this.history.length === 0) {
      this.messagesEl.createDiv({
        text: "Pick an agent and ask something. Turn on Include current note to give it the open note.",
        cls: "agents-hub-muted",
      });
    }
  }

  async send(presetText) {
    if (this.busy) return;
    const text = (presetText != null ? presetText : this.input.value).trim();
    if (!text) return;
    if (!this.agentId) {
      new obsidian.Notice(this.agents.length ? "Pick an agent first." : "No agents found. Check the plugin settings.");
      return;
    }

    let contexts = [];
    try {
      if (this.attached) contexts.push(this.attached.message);
      if (this.includeBox.checked) {
        const file = this.app.workspace.getActiveFile();
        if (file) contexts.push(await noteContext(this.app, file));
      }
      this.plugin.hub._config();
    } catch (err) {
      new obsidian.Notice(err.message);
      return;
    }

    this.history.push({ role: "user", content: text });
    this.input.value = "";
    this.renderMessages();
    const pending = this.messagesEl.createDiv({ text: "Thinking...", cls: "agents-hub-bubble agents-hub-agent-bubble agents-hub-thinking" });
    this.scrollDown();
    this.setBusy(true);

    try {
      const res = await this.plugin.hub.chat(this.agentId, this.history, contexts);
      this.history.push({ role: "assistant", content: res.answer || "(empty answer)", runId: res.runId, question: text });
    } catch (err) {
      pending.remove();
      // A dangling user turn would break the user/assistant alternation, so it is
      // taken back out and the text returns to the input box for a retry.
      this.history.pop();
      this.input.value = text;
      new obsidian.Notice(err.message, 8000);
      this.renderMessages();
      this.setBusy(false);
      return;
    }
    this.setBusy(false);
    this.renderMessages();
  }

  setBusy(on) {
    this.busy = on;
    this.sendBtn.disabled = on;
    this.sendBtn.setText(on ? "Thinking..." : "Send");
  }

  scrollDown() {
    this.messagesEl.scrollTop = this.messagesEl.scrollHeight;
  }

  renderMessages() {
    this.messagesEl.empty();
    if (!this.history.length) return this.renderEmpty();
    for (const msg of this.history) {
      if (msg.role === "user") {
        this.messagesEl.createDiv({ text: msg.content, cls: "agents-hub-bubble agents-hub-user-bubble" });
        continue;
      }
      const bubble = this.messagesEl.createDiv({ cls: "agents-hub-bubble agents-hub-agent-bubble" });
      const body = bubble.createDiv({ cls: "agents-hub-answer" });
      const sourcePath = this.app.workspace.getActiveFile() ? this.app.workspace.getActiveFile().path : "";
      obsidian.MarkdownRenderer.render(this.app, msg.content, body, sourcePath, this);
      if (msg.runId) bubble.createDiv({ text: "run " + msg.runId, cls: "agents-hub-run agents-hub-muted" });
      const actions = bubble.createDiv({ cls: "agents-hub-actions" });
      this.action(actions, "Insert", () => insertText(this.app, this.plugin, msg.content));
      this.action(actions, "Copy", async () => {
        try {
          await navigator.clipboard.writeText(msg.content);
          new obsidian.Notice("Copied.");
        } catch (_) {
          new obsidian.Notice("Could not copy to the clipboard.");
        }
      });
      this.action(actions, "New note", async () => {
        const file = await createNote(this.app, noteNameFromQuestion(msg.question), msg.content + "\n");
        new obsidian.Notice("Created " + file.path);
        this.app.workspace.getLeaf(false).openFile(file);
      });
    }
    this.scrollDown();
  }

  action(parent, label, fn) {
    const btn = parent.createEl("button", { text: label, cls: "agents-hub-action" });
    btn.addEventListener("click", async () => {
      try {
        await fn();
      } catch (err) {
        new obsidian.Notice(err.message || String(err));
      }
    });
  }
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

class AgentsHubSettingTab extends obsidian.PluginSettingTab {
  constructor(app, plugin) {
    super(app, plugin);
    this.plugin = plugin;
    this.agents = [];
  }

  display() {
    const { containerEl } = this;
    containerEl.empty();
    const s = this.plugin.settings;

    new obsidian.Setting(containerEl)
      .setName("Hub URL")
      .setDesc("Where your Agents Hub runs, for example http://localhost:8000.")
      .addText((t) =>
        t.setPlaceholder("http://localhost:8000").setValue(s.hubUrl).onChange(async (v) => {
          s.hubUrl = v;
          await this.plugin.saveSettings();
        })
      );

    new obsidian.Setting(containerEl)
      .setName("API key")
      .setDesc("Create an API key on the hub's Account page. A hub token works too.")
      .addText((t) => {
        t.inputEl.type = "password";
        t.setPlaceholder("ah_...").setValue(s.apiKey).onChange(async (v) => {
          s.apiKey = v;
          await this.plugin.saveSettings();
        });
      });

    new obsidian.Setting(containerEl)
      .setName("Workspace")
      .setDesc("Optional. Leave empty to use the key's own workspace.")
      .addText((t) =>
        t.setValue(s.workspace).onChange(async (v) => {
          s.workspace = v;
          await this.plugin.saveSettings();
        })
      );

    const agentSetting = new obsidian.Setting(containerEl)
      .setName("Default agent")
      .setDesc("Used by the rewrite command and as the panel's first choice.");
    agentSetting.addDropdown((d) => {
      this.agentDropdown = d;
      this.fillAgentDropdown();
      d.onChange(async (v) => {
        s.defaultAgent = v;
        await this.plugin.saveSettings();
      });
    });
    agentSetting.addButton((b) =>
      b.setButtonText("Refresh").onClick(async () => {
        await this.refreshAgents(true);
      })
    );

    new obsidian.Setting(containerEl)
      .setName("Insert mode")
      .setDesc("Where the Insert button puts an answer in the open note.")
      .addDropdown((d) =>
        d
          .addOption("cursor", "At the cursor")
          .addOption("append", "At the end of the note")
          .setValue(s.insertMode)
          .onChange(async (v) => {
            s.insertMode = v;
            await this.plugin.saveSettings();
          })
      );

    new obsidian.Setting(containerEl)
      .setName("Test connection")
      .setDesc("Asks the hub for its agent list.")
      .addButton((b) =>
        b.setButtonText("Test").onClick(async () => {
          try {
            const agents = await this.plugin.hub.listAgents();
            new obsidian.Notice(
              agents.length ? `Connected. ${agents.length} agent${agents.length === 1 ? "" : "s"} available.` : "Connected, but the hub has no agents."
            );
          } catch (err) {
            new obsidian.Notice(err.message, 8000);
          }
        })
      );

    this.refreshAgents(false);
  }

  fillAgentDropdown() {
    const d = this.agentDropdown;
    if (!d) return;
    d.selectEl.empty();
    const saved = this.plugin.settings.defaultAgent;
    d.addOption("", "None");
    // A saved agent stays selectable even when the list could not be loaded,
    // so a hub hiccup never silently clears the setting.
    if (saved && !this.agents.some((a) => a.id === saved)) d.addOption(saved, saved);
    for (const a of this.agents) d.addOption(a.id, a.name);
    d.setValue(saved || "");
  }

  async refreshAgents(announce) {
    try {
      this.agents = await this.plugin.hub.listAgents();
      if (announce) new obsidian.Notice(this.agents.length ? `${this.agents.length} agents loaded.` : "The hub has no agents.");
    } catch (err) {
      this.agents = [];
      if (announce) new obsidian.Notice(err.message, 8000);
    }
    this.fillAgentDropdown();
  }
}

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

class AgentsHubPlugin extends obsidian.Plugin {
  async onload() {
    await this.loadSettings();
    this.hub = new HubClient(() => this.settings);
    this.lastMarkdownView = null;

    this.registerView(VIEW_TYPE, (leaf) => new ChatView(leaf, this));
    this.addSettingTab(new AgentsHubSettingTab(this.app, this));
    this.addRibbonIcon("bot", "Open Agents Hub chat", () => this.openPanel());

    // The chat panel steals focus, so remember the last note editor for Insert.
    this.registerEvent(
      this.app.workspace.on("active-leaf-change", (leaf) => {
        if (leaf && leaf.view instanceof obsidian.MarkdownView) this.lastMarkdownView = leaf.view;
      })
    );

    this.addCommand({
      id: "open-chat",
      name: "Open Agents Hub chat",
      callback: () => this.openPanel(),
    });

    this.addCommand({
      id: "ask-about-note",
      name: "Ask an agent about this note",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (!file) return false;
        if (!checking) {
          noteContext(this.app, file).then((msg) => this.openPanel(msg, "note " + file.basename));
        }
        return true;
      },
    });

    this.addCommand({
      id: "ask-about-selection",
      name: "Ask an agent about the selection",
      editorCallback: (editor, ctx) => {
        const selection = editor.getSelection();
        if (!selection.trim()) {
          new obsidian.Notice("Select some text first.");
          return;
        }
        this.openPanel(selectionContext(ctx.file, selection), "selection from " + (ctx.file ? ctx.file.basename : "the note"));
      },
    });

    this.addCommand({
      id: "rewrite-selection",
      name: "Rewrite selection with the default agent",
      editorCallback: (editor, ctx) => {
        const selection = editor.getSelection();
        if (!selection.trim()) {
          new obsidian.Notice("Select some text first.");
          return;
        }
        try {
          this.hub._config();
        } catch (err) {
          new obsidian.Notice(err.message);
          return;
        }
        new RewriteModal(this.app, this, editor, selection, ctx.file).open();
      },
    });

    this.addCommand({
      id: "summarise-note",
      name: "Summarise this note into a new note",
      checkCallback: (checking) => {
        const file = this.app.workspace.getActiveFile();
        if (!file) return false;
        if (!checking) this.summarise(file);
        return true;
      },
    });
  }

  async loadSettings() {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings() {
    await this.saveData(this.settings);
  }

  async openPanel(contextMessage, label) {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE)[0];
    if (!leaf) {
      leaf = workspace.getRightLeaf(false);
      await leaf.setViewState({ type: VIEW_TYPE, active: true });
    }
    workspace.revealLeaf(leaf);
    if (contextMessage && leaf.view instanceof ChatView) leaf.view.attach(contextMessage, label);
    return leaf;
  }

  async summarise(file) {
    try {
      const agent = this.settings.defaultAgent;
      if (!agent) throw new HubError("Choose a default agent in the plugin settings first.");
      const ctx = await noteContext(this.app, file);
      const notice = new obsidian.Notice("Summarising " + file.basename + "...", 0);
      try {
        const res = await this.hub.chat(
          agent,
          [{ role: "user", content: "Summarise this note. Keep the key points and any decisions or open questions." }],
          [ctx]
        );
        const created = await createNote(this.app, "Summary of " + noteNameFromQuestion(file.basename), res.answer + "\n");
        new obsidian.Notice("Created " + created.path);
        this.app.workspace.getLeaf(false).openFile(created);
      } finally {
        notice.hide();
      }
    } catch (err) {
      new obsidian.Notice(err.message, 8000);
    }
  }
}

module.exports = AgentsHubPlugin;
// Obsidian reads the class from module.exports; tests reach the pure helpers through this property.
module.exports.helpers = {
  normalizeBaseUrl,
  buildHeaders,
  parseAgents,
  trimHistory,
  buildContextMessage,
  buildRequestBody,
  extractError,
  extractAnswer,
  extractRunId,
  noteNameFromQuestion,
};
