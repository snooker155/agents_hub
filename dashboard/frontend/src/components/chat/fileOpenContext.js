import { createContext } from 'react';

// Where a click on a file a turn changed takes it. Without a provider, to
// the file's page (its link). A page that shows the run's artifacts beside
// the chat (the Chat page, a flow's run page, a team's) provides
// `(path, file) => void` and shows the file there instead, its own page one
// more click away; `file` is the change as the reply lists it.
export const FileOpenContext = createContext(null);
