import { useEffect, useState } from 'react';
import { getLocalServers } from '../../api/localModels';

// How often the catalog asks whether the local servers answer.
const POLL_MS = 30000;

/** `{ollama: {ok, url}, lmstudio: {...}, 'hub-local': {...}}`, refreshed every 30 s. */
export default function useLocalServers() {
  const [servers, setServers] = useState({});
  useEffect(() => {
    let alive = true;
    const load = () => getLocalServers()
      .then(({ data }) => { if (alive) setServers(data?.servers || {}); })
      .catch(() => {});
    load();
    const timer = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  return servers;
}
