/**
 * How each MCP client is pointed at the hub's /v1/mcp. The same formats as
 * `ah mcp connect` (cli/mcp_clients.py): a format changed there changes here.
 * The server is the same for every client; only the client's own
 * configuration differs.
 */

export const MCP_CLIENTS = ['claude-code', 'cursor', 'vscode', 'windsurf', 'claude-desktop', 'codex', 'gemini', 'other'];

const hasHeaders = (headers) => Object.keys(headers).length > 0;

function entryFor(client, url, headers) {
  const withHeaders = hasHeaders(headers) ? { headers } : {};
  if (client === 'vscode') return { type: 'http', url, ...withHeaders };
  if (client === 'windsurf') return { serverUrl: url, ...withHeaders };
  if (client === 'gemini') return { httpUrl: url, ...withHeaders };
  if (client === 'claude-desktop') {
    // Claude Desktop starts local servers from its file; a remote one with a
    // header goes through the mcp-remote bridge, values passed through env.
    const args = ['-y', 'mcp-remote', url];
    const env = {};
    Object.entries(headers).forEach(([name, value], i) => {
      const v = name.toLowerCase() === 'authorization' ? 'AUTH_HEADER' : `HUB_HEADER_${i}`;
      args.push('--header', `${name}:\${${v}}`);
      env[v] = value;
    });
    return { command: 'npx', args, ...(hasHeaders(env) ? { env } : {}) };
  }
  return { url, ...withHeaders };
}

const quote = (s) => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`);

/** What to paste for `client`, as text. */
export function mcpSnippet(client, { url, headers, serverName = 'agents-hub' }) {
  if (client === 'claude-code') {
    const parts = ['claude', 'mcp', 'add', '--transport', 'http', serverName, url];
    Object.entries(headers).forEach(([k, v]) => parts.push('--header', `${k}: ${v}`));
    return parts.map(quote).join(' ');
  }
  if (client === 'codex') {
    const lines = [`[mcp_servers.${serverName}]`, `url = ${JSON.stringify(url)}`];
    if (hasHeaders(headers)) {
      const pairs = Object.entries(headers).map(([k, v]) => `${JSON.stringify(k)} = ${JSON.stringify(v)}`).join(', ');
      lines.push(`http_headers = { ${pairs} }`);
    }
    return lines.join('\n');
  }
  if (client === 'other') {
    return [`URL: ${url}`, 'Transport: Streamable HTTP',
      ...Object.entries(headers).map(([k, v]) => `Header: ${k}: ${v}`)].join('\n');
  }
  const key = client === 'vscode' ? 'servers' : 'mcpServers';
  return JSON.stringify({ [key]: { [serverName]: entryFor(client, url, headers) } }, null, 2);
}

/** A link that opens the client with the server filled in, where it has one. */
export function mcpInstallLink(client, { url, headers, serverName = 'agents-hub' }) {
  if (client === 'cursor') {
    return `cursor://anysphere.cursor-deeplink/mcp/install?name=${encodeURIComponent(serverName)}&config=${
      btoa(JSON.stringify(entryFor('cursor', url, headers)))}`;
  }
  if (client === 'vscode') {
    return `vscode:mcp/install?${encodeURIComponent(JSON.stringify({ name: serverName, ...entryFor('vscode', url, headers) }))}`;
  }
  return '';
}
