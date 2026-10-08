# Agents Hub for Obsidian

Talk to your Agents Hub agents from inside Obsidian. Ask about the open note or a selection, rewrite text in place, and turn answers into notes.

The plugin is a single `main.js` with no build step and no dependencies. It works on desktop and mobile.

## Install

1. Download the plugin zip from the hub's Distribution page, and unpack it into `<vault>/.obsidian/plugins/agents-hub/`.
2. Or copy the three files `main.js`, `manifest.json` and `styles.css` from this folder into that same directory.
3. In Obsidian, open Settings, Community plugins, and enable Agents Hub.

## Settings

- Hub URL: where your hub runs, for example `http://localhost:8000`. Trailing slashes and a pasted `/v1` are removed.
- API key: create one on the hub's Account page. A hub token works as well. A hub in single mode (one operator, no sign in) needs none, so leave the field empty there.
- Workspace: optional. Sent as `X-Agents-Hub-Workspace`.
- Default agent: filled from the hub, with a Refresh button. The saved value is kept even if the list cannot be loaded.
- Insert mode: put an inserted answer at the cursor, or at the end of the note.
- Test connection: asks the hub for its agents and shows the count or the error.

## Commands

- Open Agents Hub chat: opens the side panel. The ribbon icon does the same.
- Ask an agent about this note: opens the panel with the note attached as context.
- Ask an agent about the selection: opens the panel with the selected text attached.
- Rewrite selection with the default agent: asks for an instruction, shows the answer, and replaces the selection only after you confirm.
- Summarise this note into a new note: creates `Summary of <note name>` in the vault root.

In the panel, pick an agent, type a question and press Ctrl or Cmd plus Enter. The Include current note toggle sends the open note as context with each question. Under every answer you can Insert it into the note, Copy it, or save it as a New note. The last 20 messages of the conversation are sent with each request. A run id appears under an answer when the hub reports one.

## Privacy

Note text is sent only to the hub URL you configure, which is your own hub. The plugin has no analytics and contacts no other server. Your API key is stored in the plugin's `data.json` inside your vault, so keep that folder out of any public sync or repository.

## Development

```
node --check main.js
npm test
```

The tests use a fake `obsidian` module (`test/fake`), so they run with plain Node and cover the pure helpers: request building, model parsing, history trimming, context messages and error extraction.

## Submit to the Obsidian community catalog

1. Put these files at the root of a GitHub repository: `main.js`, `manifest.json`, `styles.css`, `versions.json` and this README.
2. Create a release whose tag equals the version in `manifest.json` (for example `0.1.0`), and attach `main.js`, `manifest.json` and `styles.css` as release assets. The workflow `.github/workflows/obsidian-plugin.yml` does this for tags named `obsidian-*`; for a standalone repository, tag with the bare version instead.
3. Open a pull request against `obsidianmd/obsidian-releases` that adds an entry to `community-plugins.json` with the plugin `id`, `name`, `author`, `description` and the `repo` as `owner/name`.
4. Respond to the review comments from the Obsidian team.
