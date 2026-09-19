import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  BrainCircuit, CheckCircle2, ChevronDown, ChevronUp, Clock, FileText,
  Loader, Mail, Radio, Terminal, XCircle, Zap,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import {
  SPEECH_ARGS, formatArgs, initials, shortText, speechOf, toneFor,
} from './cast';
import { eventLines } from './events';
import { isLiveStatus } from './status';

/**
 * A run, read as the conversation it actually is.
 *
 * A simulation is agents talking to each other and reaching for tools, which is
 * the same shape as a chat with an agent — so it is rendered the same way:
 * one turn per bubble, the agent's thought collapsed above it, its action and
 * what the world made of that below it. The tick-at-a-time inspector that used
 * to live here answered "what happened on tick 7" and nothing else; the thing
 * you actually want to read is the whole scene from the top.
 *
 * The two activation modes read differently on purpose:
 *   * synchronous — everybody acts every tick, so the tick is a real boundary
 *     in the story and gets a divider. Turns inside one tick are simultaneous,
 *     and the divider is what says so.
 *   * triggered — an agent acts only when something reached it, so ticks are
 *     just the clock. No dividers: one flat chat, each turn stamped with the
 *     tick it landed on.
 *
 * The world's own events are not part of the conversation — nobody said them —
 * so they live behind their own tab (`EventFeed`) instead of interleaving into
 * the dialogue or sitting next to it shouting for attention.
 */

/**
 * A name, in the colour that name always has.
 *
 * The tone is the same hash the avatar and the speaker's own heading use, so
 * "Old Tam" is the same colour whether he is the one speaking, the one being
 * answered, or the one whose message is quoted back at the top of a turn.
 * Following who is talking to whom across a tick is the whole reading, and
 * matching a name to a bubble by reading it letter by letter is the part that
 * made a busy tick unreadable.
 *
 * Only where a name is addressed to somebody, though — not inside a call's
 * arguments. ``agent=Old Tam`` is machinery, it reads as one grey line of
 * key=value, and a colour in the middle of it only breaks the line up.
 *
 * A tint, not a highlight: it sits on the tone's darkest shade, so it reads as
 * the same grey-ish text as its neighbours until you are looking for it.
 */
function AgentName({ name, className = '' }) {
  return (
    <span className={`font-semibold ${toneFor(name).name} ${className}`}>{name}</span>
  );
}


/** Why this agent got a turn. Silent in a synchronous world, where the answer
    is always "because the clock ticked" and saying so would be noise. */
function TriggerChips({ triggers }) {
  const reasons = (triggers || []).filter((r) => r && r !== 'tick');
  if (!reasons.length) return null;
  return (
    <>
      {reasons.map((r, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 text-xs font-medium"
        >
          <Zap className="w-3.5 h-3.5" />{r}
        </span>
      ))}
    </>
  );
}


/**
 * What reached this agent before it thought — the mail its turn is an answer to.
 *
 * Above the thought rather than below it because that is the order it
 * happened in: an agent is woken by what was said to it, reasons about it,
 * and only then acts. Without it a turn reads as an agent talking to itself.
 */
function InboxCard({ messages }) {
  const { t } = useI18n();
  const list = (messages || []).filter((m) => m && (m.text || m.from));
  if (!list.length) return null;
  return (
    /* A rule in the margin rather than a card: this is the one part of a turn
       that is not the agent's own work — words somebody else already said,
       repeated back as context. It has to be findable, not noticed. */
    <div className="border-l-2 border-gray-200 pl-2.5 py-0.5 space-y-0.5">
      <div className="flex items-center gap-1.5 text-[11px] text-gray-400">
        <Mail className="w-3 h-3" />
        {t('playground.transcript.received', { count: list.length })}
      </div>
      {list.map((m, i) => (
        <div key={i} className="text-sm text-gray-500 break-words">
          <AgentName name={m.from || '?'} />
          {': '}
          <span className="whitespace-pre-wrap">{m.text}</span>
        </div>
      ))}
    </div>
  );
}


/** The agent's reasoning — the same collapsed thought card the chat uses. */
function ThoughtCard({ text }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-gray-200">
      <button
        type="button" onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-1.5 text-left"
      >
        <BrainCircuit className="w-3.5 h-3.5 text-violet-400 shrink-0" />
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide shrink-0">
          {t('playground.transcript.thought')}
        </span>
        {!open && (
          <span className="text-sm text-gray-400 truncate flex-1">
            {text.replace(/\s+/g, ' ').trim()}
          </span>
        )}
        {open
          ? <ChevronUp className="w-4 h-4 text-gray-400 ml-auto shrink-0" />
          : <ChevronDown className="w-4 h-4 text-gray-400 shrink-0" />}
      </button>
      {open && (
        <div className="px-3 pb-2.5 text-sm text-gray-600 whitespace-pre-wrap leading-relaxed">
          {text}
        </div>
      )}
    </div>
  );
}


/**
 * One submitted action and what the environment did with it.
 *
 * Rendered as a tool call because that is exactly what it is — the environment
 * API is the agent's whole toolset — and the resolution is its output. A
 * refusal is an ordinary outcome (selling stock you do not own), so it is shown
 * as a failed call rather than as an error.
 */
function ActionCard({ action, resolutions, pending }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const args = formatArgs(action.args);
  const refused = resolutions.some((r) => !r.ok);
  return (
    /* A call the agent made is machinery: grey, like the thought above it. A
       refusal is not — the world said no, which is a fact about the run and
       keeps its colour. */
    <div className={`rounded-lg border ${refused ? 'border-red-200 bg-red-50/50' : 'border-gray-200'}`}>
      <button
        type="button" onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-3 py-1.5 text-left"
      >
        <Terminal className={`w-3.5 h-3.5 shrink-0 ${refused ? 'text-red-500' : 'text-amber-500'}`} />
        <span className={`text-sm font-semibold shrink-0 ${refused ? 'text-red-700' : 'text-gray-600'}`}>
          {action.action || t('playground.transcript.action')}
        </span>
        {!open && args && (
          <span className="text-sm text-gray-400 truncate flex-1">{shortText(args, 90)}</span>
        )}
        {pending && (
          <span className="inline-flex items-center gap-1 text-xs text-gray-400 shrink-0">
            <Clock className="w-3.5 h-3.5" />{t('playground.transcript.pending')}
          </span>
        )}
        {open
          ? <ChevronUp className="w-4 h-4 text-gray-400 ml-auto shrink-0" />
          : <ChevronDown className="w-4 h-4 text-gray-400 ml-auto shrink-0" />}
      </button>
      {open && (
        <div className="px-2.5 pb-2 space-y-1">
          {args && (
            <div className="text-sm text-gray-600 whitespace-pre-wrap break-words">
              <span className="text-gray-400">{t('playground.transcript.argsLabel')}</span> {args}
            </div>
          )}
          {!resolutions.length && !pending && (
            <div className="text-sm text-gray-400">{t('playground.transcript.noResolution')}</div>
          )}
        </div>
      )}
      {resolutions.map((r, i) => (
        <div
          key={i}
          className={`flex items-start gap-1.5 px-2.5 pb-2 text-sm ${r.ok ? 'text-gray-500' : 'text-red-700'}`}
        >
          {r.ok
            ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-500" />
            : <XCircle className="w-4 h-4 mt-0.5 shrink-0 text-red-500" />}
          <span className="min-w-0 break-words">{r.message}</span>
        </div>
      ))}
    </div>
  );
}


/** What the agent saw and what the model literally wrote — the debugging half
    of a turn, folded away because it is not part of reading the scene. */
function TurnDetails({ decision }) {
  const { t } = useI18n();
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 p-2.5 space-y-2">
      <div>
        <div className="font-bold text-gray-500 uppercase text-xs">{t('playground.observation')}</div>
        <pre className="text-xs text-gray-600 bg-white rounded p-2 max-h-48 overflow-auto">
          {JSON.stringify(decision.observation, null, 2)}
        </pre>
      </div>
      {decision.raw_output && (
        <div>
          <div className="font-bold text-gray-500 uppercase text-xs">{t('playground.rawModelOutput')}</div>
          <pre className="text-xs text-gray-600 bg-white rounded p-2 max-h-32 overflow-auto whitespace-pre-wrap">
            {decision.raw_output}
          </pre>
        </div>
      )}
      <div className="text-xs text-gray-400">
        {t('playground.inspectorMeta', {
          ms: decision.duration_ms,
          inbound: decision.inbound_tokens,
          outbound: decision.outbound_tokens,
        })}
        {/* A streamed completion does not always carry usage; when it does not
            the counts are our estimate and must not read as measured. */}
        {decision.tokens_estimated && ` · ${t('playground.tokensEstimated')}`}
      </div>
    </div>
  );
}


/** One agent's turn: what woke it, what it thought, said, did — and the way
    into its own run log, since a turn here is an ordinary agent run. */
function Turn({ entry }) {
  const { t } = useI18n();
  const [details, setDetails] = useState(false);
  const d = entry.decision;
  const tone = toneFor(d.agent);
  const speech = speechOf(d.action);
  return (
    <div className="flex gap-2.5">
      <div className={`shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold ${tone.avatar}`}>
        {initials(d.agent)}
      </div>
      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className={`text-base font-bold ${tone.name}`}>{d.agent}</span>
          {entry.showTick && (
            <span className="text-xs font-mono text-gray-400">
              {t('playground.tickLabelShort', { tick: entry.tick })}
            </span>
          )}
          <TriggerChips triggers={d.triggers} />
          {entry.live && (
            <span className="inline-flex items-center gap-1 text-xs text-blue-600">
              <Radio className="w-3.5 h-3.5 animate-pulse" />{t('playground.justIn')}
            </span>
          )}
          <span className="ml-auto flex items-center gap-1.5 shrink-0 text-xs text-gray-400">
            {d.duration_ms ? `${(d.duration_ms / 1000).toFixed(1)}s` : ''}
            {d.cost ? `· $${d.cost.toFixed(4)}` : ''}
            <button
              type="button" onClick={() => setDetails((o) => !o)}
              title={t('playground.transcript.details')}
              className={`p-0.5 rounded ${details ? 'text-indigo-600' : 'hover:text-gray-700'}`}
            >
              {details ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
            {d.run_id && (
              <Link
                to={`/messages/${d.run_id}`} title={t('playground.openTurnLog')}
                className="p-0.5 hover:text-indigo-600"
              >
                <FileText className="w-4 h-4" />
              </Link>
            )}
          </span>
        </div>

        {/* The turn in the order it happened, and in the order the chat with
            an agent reads: what it was told, what it thought, what it called —
            and, last, what it actually said. The spoken line is the reply; a
            reply that is followed by its own machinery reads like a footnote
            to a tool call rather than the thing the agent came to say. */}
        <InboxCard messages={d.observation?.messages} />

        {d.reasoning && <ThoughtCard text={d.reasoning} />}

        {d.action && (
          <ActionCard action={d.action} resolutions={entry.resolutions} pending={entry.live} />
        )}

        {speech && (
          <div className={`border rounded-2xl rounded-tl-sm px-3.5 py-2.5 shadow-sm ${tone.bubble}`}>
            {speech.to && (
              <div className="text-xs mb-0.5">
                <span className="font-semibold text-gray-400">
                  {t('playground.transcript.toLabel')}
                </span>{' '}
                <AgentName name={speech.to} />
              </div>
            )}
            <div className="text-base text-gray-900 whitespace-pre-wrap leading-relaxed break-words">
              {speech.text}
            </div>
          </div>
        )}

        {d.error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-sm text-red-700 break-words">
            {d.error}
          </div>
        )}

        {details && <TurnDetails decision={d} />}
      </div>
    </div>
  );
}


/** The thought as it is being written: the tail of it, scrolled to the end,
    the way the chat shows a model thinking out loud. Open rather than folded —
    a thought nobody can see is indistinguishable from a hung run. */
function LiveThought({ text }) {
  const { t } = useI18n();
  const boxRef = useRef(null);
  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [text]);
  return (
    <div className="rounded-lg border border-gray-200">
      <div className="flex items-center gap-1.5 px-3 pt-1.5">
        <BrainCircuit className="w-3.5 h-3.5 text-violet-400 shrink-0" />
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          {t('playground.transcript.thinking')}
        </span>
      </div>
      <div
        ref={boxRef}
        className="px-3 pb-2.5 pt-1 max-h-24 overflow-y-auto text-sm text-gray-600 whitespace-pre-wrap leading-relaxed break-words"
      >
        {text}
      </div>
    </div>
  );
}


/** Three bouncing dots — the same "working" tell the main chat uses. */
function WorkingDots() {
  return (
    <span className="flex gap-1">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
          style={{ animationDelay: `${delay}ms` }}
        />
      ))}
    </span>
  );
}


/**
 * An agent mid-turn: its place in the transcript, held from the moment it was
 * woken until its decision lands.
 *
 * A tick is as slow as its slowest agent. Waiting for the whole tick to be
 * written before showing anything meant a minute of blank page with no way to
 * tell work from a hang — and no way to see that five agents are all thinking
 * at once, which is most of what there is to watch. So each woken agent gets
 * its bubble immediately, carrying the mail it was woken for, and fills in as
 * the model writes: the thought first, the action as soon as it is named.
 * When the finished decision arrives this bubble is replaced by the real turn.
 */
function WorkingTurn({ entry }) {
  const { t } = useI18n();
  const tone = toneFor(entry.agent);
  return (
    <div className="flex gap-2.5">
      <div className={`shrink-0 w-9 h-9 rounded-full flex items-center justify-center text-sm font-bold ${tone.avatar}`}>
        {initials(entry.agent)}
      </div>
      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className={`text-base font-bold ${tone.name}`}>{entry.agent}</span>
          {entry.showTick && (
            <span className="text-xs font-mono text-gray-400">
              {t('playground.tickLabelShort', { tick: entry.tick })}
            </span>
          )}
          <TriggerChips triggers={entry.triggers} />
          <span className="inline-flex items-center gap-1.5 text-xs text-gray-400">
            {entry.action
              ? t('playground.transcript.submitting', { action: entry.action })
              : t('playground.transcript.working')}
            <WorkingDots />
          </span>
        </div>

        <InboxCard messages={entry.messages} />
        {entry.reasoning
          ? <LiveThought text={entry.reasoning} />
          : (
            <div className="rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-400">
              {t('playground.transcript.awaitingModel')}
            </div>
          )}
      </div>
    </div>
  );
}


/** The tick boundary in a synchronous world: everything in one group happened
    at the same moment, which is exactly what the divider is there to say. */
function TickDivider({ tick, current }) {
  const { t } = useI18n();
  return (
    <div className="flex items-center gap-2 pt-1">
      <span className="h-px flex-1 bg-gray-100" />
      <span
        className={`text-xs font-bold uppercase tracking-wide px-1.5 py-0.5 rounded ${
          current ? 'bg-indigo-100 text-indigo-700' : 'text-gray-400'
        }`}
      >
        {t('playground.tickLabelShort', { tick })}
      </span>
      <span className="h-px flex-1 bg-gray-100" />
    </div>
  );
}


/**
 * The whole run as one scrolling conversation, up to the scrubber's tick.
 *
 * Newest first: the run is read as it happens, and the turn you are waiting
 * for is the one that just landed, so it belongs at the top where the eye
 * already is rather than at the end of an ever-growing scroll.
 *
 * `inFlight` holds decisions that arrived on the stream before their tick was
 * written: a tick is as slow as its slowest agent, and without them the page
 * looks frozen while agents visibly finish one by one. They only belong at the
 * head of the transcript while following — scrubbed back to tick 3, "just in"
 * is somebody else's tick.
 */
export function ScenarioTranscript({
  ticks, cursor, activation = 'synchronous', inFlight = [], following = true,
  // Agents that have been woken and have not answered yet — see `WorkingTurn`.
  // Like `inFlight`, they are only the head of a followed transcript.
  activity = [],
  waitingForTrigger = false, emptyLabel = '',
  // The run's own status, so an empty transcript can say whether there is
  // nothing to read yet or nothing to read at all.
  status = '',
  // How tall the scroller is allowed to be. The default stands on its own in
  // an ordinary card; a page that gives the transcript the rest of the screen
  // passes a flex-fill instead, since only the page knows how much is left.
  heightClass = 'max-h-[calc(100vh-24rem)] min-h-[18rem]',
}) {
  const { t } = useI18n();
  const boxRef = useRef(null);
  const grouped = activation !== 'triggered';

  // Built in the order the run happened, then turned around — inside a tick as
  // well as between ticks, so that "higher up" means "later" everywhere and the
  // reader never has to switch direction mid-page.
  const groups = useMemo(() => (
    ticks.slice(0, cursor + 1).map((tk) => ({
      tick: tk.tick,
      idle: tk.idle || [],
      turns: (tk.decisions || []).map((d) => ({
        key: `${tk.tick}-${d.agent}`,
        tick: tk.tick,
        decision: d,
        showTick: !grouped,
        // One decision is one action, so every resolution this agent produced
        // on this tick belongs to it.
        resolutions: (tk.resolutions || []).filter((r) => r.agent === d.agent),
      })).reverse(),
    })).reverse()
  ), [ticks, cursor, grouped]);

  const liveTurns = (following ? inFlight : []).map((d) => ({
    key: `live-${d.tick}-${d.agent}`,
    tick: d.tick,
    decision: d,
    showTick: !grouped,
    live: true,
    resolutions: [],
  })).reverse();

  // Working bubbles are the newest thing on the page, above even the turns
  // that just landed: they are the ones still moving. In the order they were
  // woken, so the column does not reshuffle itself every time one reports.
  const working = (following ? activity : []).map((a) => ({
    ...a, key: `working-${a.tick}-${a.agent}`, showTick: !grouped,
  }));

  const turnCount = groups.reduce((n, g) => n + g.turns.length, 0)
    + liveTurns.length + working.length;
  const lastTick = groups.length ? groups[0].tick : -1;

  // Pin to the newest turn. The transcript is truncated at the cursor and runs
  // newest first, so the tick being scrubbed to is always at the very top —
  // scrolling home lands on it either way, whether it arrived or was scrubbed
  // to.
  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = 0;
  }, [turnCount, cursor]);

  // An empty transcript means two opposite things, and which one it is has to
  // be on screen. A run that has not started has nothing to read; a run that
  // has started has nothing to read *yet* — the first tick is as slow as its
  // slowest agent, which is a minute of model calls, and a bare "run the
  // scenario" line under a live run reads as a broken page.
  //
  // The wait has two halves and they are told apart, because they fail
  // differently: "launching" is the scenario being built and its first prompts
  // being sent, so nothing has answered yet; "starting" is a live run whose
  // agents are thinking. A model server that never answers leaves the first
  // one on screen, which is the sentence that explains the silence.
  if (!turnCount) {
    if (isLiveStatus(status)) {
      const line = { stopping: 'stopping', starting: 'launching' }[status] || 'starting';
      return (
        <div className="flex items-start gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2.5 text-base text-blue-800">
          {status === 'stopping'
            ? <Radio className="w-4 h-4 mt-1 shrink-0 animate-pulse" />
            : <Loader className="w-4 h-4 mt-1 shrink-0 animate-spin" />}
          <span>
            {/* Waiting to be poked is not the same as waiting for a slow model:
                in a triggered world nothing will happen until something reaches
                an agent, and the page should say so rather than spin. */}
            {waitingForTrigger
              ? t('playground.waitingForTrigger')
              : t(`playground.transcript.${line}`)}
          </span>
        </div>
      );
    }
    return (
      <p className="text-base text-gray-400 italic">
        {emptyLabel || t('playground.runTheScenarioToInspect')}
      </p>
    );
  }

  return (
    <div ref={boxRef} className={`space-y-3 overflow-y-auto pr-1 ${heightClass}`}>
      {/* What the page is waiting on, and what only just arrived, sit at the
          top — they are the newest things there are. */}
      {waitingForTrigger && (
        <p className="text-base text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          {t('playground.waitingForTrigger')}
        </p>
      )}
      {working.map((entry) => <WorkingTurn key={entry.key} entry={entry} />)}
      {liveTurns.map((entry) => <Turn key={entry.key} entry={entry} />)}
      {groups.map((g) => (
        <div key={g.tick} className="space-y-3">
          {grouped && <TickDivider tick={g.tick} current={g.tick === lastTick} />}
          {g.turns.map((entry) => <Turn key={entry.key} entry={entry} />)}
          {g.idle.length > 0 && (
            <p className="text-xs text-gray-400 pl-10">
              {t('playground.idleThisTick', { names: g.idle.join(', ') })}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}


/**
 * The world's own log: what happened that nobody said.
 *
 * Events are not turns — the environment wrote them — so interleaving them into
 * the conversation would put a narrator in the middle of a dialogue. Refused
 * actions are repeated here because a refusal is a fact about the world, even
 * though the turn that caused it shows the same refusal inline.
 *
 * Nothing here is coloured as an alarm. A refusal is an ordinary outcome of a
 * simulation (selling stock you do not own), and painting the log red made a
 * routine event read as a failure. It is named instead of tinted: the word
 * says what happened, which an icon never quite does.
 *
 * The log is prose, not a status strip — it is read line by line, so it is set
 * at the page's reading size rather than the size of a caption.
 */
export function EventFeed({ ticks, cursor, heightClass = 'max-h-[calc(100vh-24rem)] min-h-[12rem]' }) {
  const { t } = useI18n();
  const boxRef = useRef(null);

  // Newest first, like the transcript beside it: the two tabs are the same run
  // read two ways, and they would be hard to hold together if one ran down the
  // page and the other up it. `eventLines` stays chronological — it is counted
  // and tested as the order the world wrote.
  const lines = useMemo(() => eventLines(ticks, cursor).reverse(), [ticks, cursor]);

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = 0;
  }, [lines.length]);

  if (!lines.length) {
    return <p className="text-base text-gray-400 italic">{t('playground.nothingYet')}</p>;
  }

  return (
    <div ref={boxRef} className={`overflow-y-auto pr-1 ${heightClass}`}>
      {lines.map((line) => (
        <div
          key={line.key}
          className="flex gap-3 py-1.5 border-b border-gray-100 last:border-0 text-base text-gray-800 leading-relaxed"
        >
          <span className="text-sm text-gray-400 font-mono shrink-0 w-6 text-right pt-1">
            {line.tick}
          </span>
          <span className="min-w-0 break-words">
            {line.kind === 'refused' && (
              <span className="mr-1.5 px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-xs font-semibold uppercase tracking-wide align-[2px]">
                {t('playground.transcript.refused')}
              </span>
            )}
            {line.text}
          </span>
        </div>
      ))}
    </div>
  );
}

export default ScenarioTranscript;
