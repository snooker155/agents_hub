import { createContext } from 'react';

// Where a click on a file a turn changed takes it. Without a provider, to
// the file's page (its link). A page that shows the run's artifacts beside
// the chat (a flow's run page) provides `(path) => void` and shows the file
// there instead, its own page one more click away.
export const FileOpenContext = createContext(null);
