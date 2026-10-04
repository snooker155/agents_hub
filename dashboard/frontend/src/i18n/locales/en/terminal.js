// The terminal panel: a shell in a run's or a service replica's container
// (components/terminal/, docs/terminal.md).
export default {
  title: 'Terminal',
  open: 'Terminal',
  openRunHint: 'Open a shell in this run\'s container',
  openReplicaHint: 'Open a shell in this replica\'s container',
  status: {
    idle: 'Starting',
    connecting: 'Connecting',
    reconnecting: 'Reconnecting',
    open: 'Connected',
    ended: 'Session ended',
    taken: 'Opened elsewhere',
    error: 'Not available',
  },
  hide: 'Hide',
  hideHint: 'Hide the panel. The shell keeps running for {{seconds}} s, so opening the terminal again comes back to it.',
  end: 'End session',
  endHint: 'Close the shell now',
  newSession: 'New session',
  notices: {
    resumed: 'session resumed, recent output replayed',
    expired: 'the previous session has ended, starting a new one',
    takenOver: 'this session was opened in another window',
  },
  reasons: {
    exited: 'the shell exited (code {{code}})',
    closed: 'the session was closed',
    grace: 'no one reconnected in time, the session has ended',
    idle: 'the session was idle too long and has ended',
    shutdown: 'the hub restarted, the session has ended',
    expired: 'the connection could not be restored in time, the session has ended',
  },
};
