// Makes require("obsidian") resolve to the fake module, because the real one
// is provided by the app at runtime and is not installed on disk.
const Module = require("node:module");
const path = require("node:path");

const fake = path.join(__dirname, "fake", "obsidian.js");
const original = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === "obsidian") return fake;
  return original.call(this, request, ...rest);
};
