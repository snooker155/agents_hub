import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useWorkspace } from './workspace';

/**
 * A record's page (a flow, a loop, a team, an agent...) when the workspace is
 * switched while it is open: the record is looked for in the new workspace's
 * list, the one its list page shows, and a record that list does not hold
 * sends the page to the list (`listPath`). One it holds stays, and the page
 * loads what it shows for the new workspace itself (its effects follow
 * `selectedWorkspace`).
 *
 * `isPresent(workspace, workspaceFilter)` answers true or false, or a
 * promise of it. Only a switch is checked, not the workspace the page opened
 * in, and a list that cannot be read leaves the page where it is.
 *
 * Returns `checking`: true from a switch until the answer, so the page can
 * keep its loader rather than show the record in a workspace it may leave.
 */
export const workspaceFilterOf = (workspace) => (
  workspace === 'default' ? undefined : workspace || undefined
);

export const listHolds = (items, id, key = 'id') => (
  (items || []).some((item) => String(item?.[key]) === String(id))
);

export default function useWorkspaceSwitchGuard({ listPath, isPresent }) {
  const { selectedWorkspace } = useWorkspace();
  const navigate = useNavigate();
  const isPresentRef = useRef(isPresent);
  useEffect(() => { isPresentRef.current = isPresent; });
  const seenRef = useRef(selectedWorkspace);
  const [checkedFor, setCheckedFor] = useState(selectedWorkspace);

  useEffect(() => {
    if (seenRef.current === selectedWorkspace) return undefined;
    seenRef.current = selectedWorkspace;
    // No workspace chosen (the remembered one dropped, the first one not yet
    // picked) filters nothing, so the record is not looked for.
    if (!selectedWorkspace) return undefined;
    // An answer counts only while its workspace is still the chosen one (a
    // second switch before it came back asks again).
    const current = () => seenRef.current === selectedWorkspace;
    Promise.resolve()
      .then(() => isPresentRef.current(selectedWorkspace, workspaceFilterOf(selectedWorkspace)))
      .then((present) => {
        if (current() && present === false) navigate(listPath, { replace: true });
      })
      .catch(() => { /* the list could not be read: stay */ })
      .then(() => { if (current()) setCheckedFor(selectedWorkspace); });
    return undefined;
  }, [selectedWorkspace, listPath, navigate]);

  return { checking: Boolean(selectedWorkspace) && checkedFor !== selectedWorkspace };
}
