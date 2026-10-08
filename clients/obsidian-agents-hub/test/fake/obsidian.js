// A stand-in for the `obsidian` module, which only exists inside the app.
// The tests exercise pure helpers, so the classes only need to be extendable.
class Empty {
  constructor() {}
}
module.exports = {
  Plugin: Empty,
  ItemView: Empty,
  Modal: Empty,
  PluginSettingTab: Empty,
  Setting: Empty,
  Notice: Empty,
  MarkdownRenderer: { render: async () => {} },
  MarkdownView: Empty,
  requestUrl: async () => {
    throw new Error("requestUrl is not available in tests");
  },
  setIcon: () => {},
  Platform: {},
};
