/**
 * Workspace files as folders, by their workspace path (``meta.path``, set for
 * a file in the workspace folder, files/service.py). A file uploaded with no
 * path sits at the root. Used by the Artifacts page's tree and the workspace
 * file picker's tree.
 */

/** The root folder node: `{ name, path, children, files, count, bytes }`,
 * folders and files sorted by name, each file with its `leaf` name. */
export function buildFileTree(files) {
  const mk = (name, path) => ({ name, path, dirs: new Map(), files: [] });
  const root = mk('', '');
  for (const f of files || []) {
    const parts = String(f.meta?.path || f.name || '').split('/').filter(Boolean);
    let node = root;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const seg = parts[i];
      if (!node.dirs.has(seg)) node.dirs.set(seg, mk(seg, node.path ? `${node.path}/${seg}` : seg));
      node = node.dirs.get(seg);
    }
    node.files.push({ ...f, leaf: parts[parts.length - 1] || f.name });
  }
  const finish = (node) => {
    node.children = [...node.dirs.values()].sort((a, b) => a.name.localeCompare(b.name)).map(finish);
    node.files.sort((a, b) => a.leaf.localeCompare(b.leaf));
    node.count = node.files.length + node.children.reduce((sum, c) => sum + c.count, 0);
    node.bytes = node.files.reduce((sum, f) => sum + (f.size || 0), 0)
      + node.children.reduce((sum, c) => sum + c.bytes, 0);
    return node;
  };
  return finish(root);
}

/** Every folder path under `node`, depth first. */
export function allDirPaths(node, out = []) {
  for (const c of node.children || []) { out.push(c.path); allDirPaths(c, out); }
  return out;
}

/** Every file under `node`, its own and its folders'. */
export function filesUnder(node, out = []) {
  out.push(...(node.files || []));
  for (const c of node.children || []) filesUnder(c, out);
  return out;
}
