import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Box, Gift, Loader, MapPin, Maximize2, MessageCircle, Move, Package,
  Sparkles, ZoomIn, ZoomOut,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import { initials, shortText, toneFor } from './cast';
import { stageLayout } from './stage-model';
import WorldView from './renderers';

/**
 * The run as a place: who is standing where, holding what, doing what to whom.
 *
 * The sidebar's world view answers the same questions and answers them as
 * lists — three of them, one under another, which is fine for a glance and
 * hopeless for a scene. Rooms next to each other, characters inside them and
 * lines between the characters turn "Tam: tavern, Mira: tavern, Mira gave Tam
 * the key" into one picture you read in a second, and it is the picture the
 * simulation is actually of.
 *
 * Three layers, drawn in this order:
 *   1. **the map** — every room, and the exits between them where the world
 *      declares any. Thin and grey: it is the ground, not the news.
 *   2. **the tick** — movement arrows and interaction arcs, in the colour of
 *      the character that caused them. This is the layer that changes as you
 *      scrub, and it is the whole reason the canvas exists.
 *   3. **the cast** — a card per character inside its room, carrying what it
 *      holds, what it just did, and what it said while doing it.
 *
 * The SVG carries only layers 1 and 2; the cards are ordinary HTML on top of
 * it, so every colour goes through the same utility classes the rest of the
 * page uses and the dark theme keeps working without a second palette.
 *
 * Geometry is computed, never measured — see `stage-model.js`. Pan and zoom
 * are a single transform on the layer that holds both.
 */

const MIN_ZOOM = 0.3;
const MAX_ZOOM = 2.2;

/** A curve between two points, bowed clear of whatever is between them. */
function arcPath(from, to, bend = 0.2) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const off = Math.max(26, len * bend);
  const cx = from.x + dx / 2 - (dy / len) * off;
  const cy = from.y + dy / 2 + (dx / len) * off;
  return { d: `M ${from.x} ${from.y} Q ${cx} ${cy} ${to.x} ${to.y}`, cx, cy };
}

/** The arrowhead, as a triangle aimed along the curve's last tangent — drawn
    rather than declared as a marker, so it inherits `currentColor` the way the
    line does and needs no per-colour <marker> in the defs. */
function Arrowhead({ at, from, size = 7 }) {
  const angle = Math.atan2(at.y - from.y, at.x - from.x);
  const points = [
    [at.x, at.y],
    [at.x - size * Math.cos(angle - 0.4), at.y - size * Math.sin(angle - 0.4)],
    [at.x - size * Math.cos(angle + 0.4), at.y - size * Math.sin(angle + 0.4)],
  ].map(([x, y]) => `${x},${y}`).join(' ');
  return <polygon points={points} fill="currentColor" />;
}

/** A label on a line: painted with a halo so it stays readable over anything. */
function LineLabel({ x, y, children, className = '' }) {
  return (
    <text
      x={x} y={y} textAnchor="middle"
      className={`text-[10px] font-semibold ${className}`}
      fill="currentColor" stroke="var(--surface-card)" strokeWidth="3"
      paintOrder="stroke" style={{ fontSize: 10 }}
    >
      {children}
    </text>
  );
}

/** One thing an agent is carrying, or one thing lying on the floor. */
function Chip({ icon: Icon, label, tone = 'bg-gray-100 text-gray-600 border-gray-200' }) {
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md border text-[10px] font-medium ${tone}`}>
      {Icon && <Icon className="w-2.5 h-2.5 shrink-0" />}
      <span className="truncate max-w-[9rem]">{label}</span>
    </span>
  );
}

/** Which icon a turn gets: speech, a handover, a walk, or a plain call. */
function ActionIcon({ action, className = '' }) {
  let Icon = Sparkles;
  if (action?.speech) Icon = MessageCircle;
  else if (/give|hand|trade|offer/i.test(action?.name || '')) Icon = Gift;
  else if (/move|go|walk|travel|enter/i.test(action?.name || '')) Icon = Move;
  return <Icon className={className} />;
}

/** One character, where it stands. */
function AgentCard({ card, idle }) {
  const { t } = useI18n();
  const { agent, turn, role } = card;
  const tone = toneFor(card.name);
  const action = turn?.action;
  const inventory = agent.inventory || [];
  const stats = Object.entries(agent.stats || {});
  const refused = (turn?.results || []).some((r) => !r.ok);

  return (
    <div
      className={`absolute rounded-lg border px-2 py-1.5 transition-colors ${
        turn ? 'bg-white border-gray-200 shadow-sm' : 'bg-gray-50 border-gray-100'
      }`}
      style={{ left: card.x, top: card.y, width: card.w, height: card.h }}
    >
      <div className="flex items-center gap-2">
        {/* Sized and padded to land its centre on the model's anchor: every
            arrow on the canvas is aimed at that point. */}
        <span
          className={`relative shrink-0 w-7 h-7 rounded-full grid place-items-center text-[10px] font-bold ${tone.avatar} ${
            turn ? `ring-2 ${tone.ring}` : ''
          }`}
        >
          {initials(card.name)}
          {turn?.thinking && (
            <span className="absolute -right-0.5 -bottom-0.5 w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse ring-2 ring-[color:var(--surface-card)]" />
          )}
        </span>
        <span className="min-w-0 flex-1">
          <span className={`block text-xs font-bold truncate ${tone.name}`}>{card.name}</span>
          {(role?.role || agent.role) && (
            <span className="block text-[10px] text-gray-400 truncate">
              {role?.role || agent.role}
            </span>
          )}
        </span>
        {idle && !turn && (
          <span className="shrink-0 text-[9px] uppercase font-bold text-gray-300 tracking-wide">
            {t('playgroundStage.idle')}
          </span>
        )}
      </div>

      {(action || turn?.thinking) && (
        <div className="mt-1 flex items-center gap-1 text-[10px]">
          {turn?.thinking && !action ? (
            <>
              <Loader className="w-3 h-3 shrink-0 text-blue-500 animate-spin" />
              <span className="text-blue-600 font-semibold">{t('playgroundStage.thinking')}</span>
            </>
          ) : (
            <>
              <ActionIcon action={action}
                          className={`w-3 h-3 shrink-0 ${refused ? 'text-red-500' : tone.line}`} />
              <span className={`font-semibold ${refused ? 'text-red-600' : 'text-gray-700'}`}>
                {action.name}
              </span>
              {action.to && (
                <span className={`truncate ${toneFor(action.to).name}`}>→ {action.to}</span>
              )}
              {!action.to && action.rest && (
                <span className="text-gray-400 truncate">{shortText(action.rest, 28)}</span>
              )}
            </>
          )}
        </div>
      )}

      {action?.speech && (
        <div className={`mt-1 rounded-md border px-1.5 py-0.5 text-[10px] leading-tight truncate ${tone.bubble}`}>
          “{shortText(action.speech, 64)}”
        </div>
      )}

      {inventory.length > 0 && (
        <div className="mt-1 flex gap-1 overflow-hidden">
          {inventory.slice(0, 3).map((item) => (
            <Chip key={item} icon={Package} label={item}
                  tone="bg-amber-50 text-amber-700 border-amber-200" />
          ))}
          {inventory.length > 3 && (
            <span className="text-[10px] text-gray-400 self-center">+{inventory.length - 3}</span>
          )}
        </div>
      )}

      {stats.length > 0 && (
        <div className="mt-1 flex gap-1 overflow-hidden text-[10px] text-gray-500">
          {stats.slice(0, 4).map(([k, v]) => (
            <span key={k} className="px-1 rounded bg-gray-100 truncate">
              {k} <span className="font-bold text-gray-700">{String(v)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/** One room, with everything standing in it. */
function RoomCard({ room, idle }) {
  const { t } = useI18n();
  return (
    <div
      className="absolute rounded-xl border border-gray-200 bg-white/95 backdrop-blur-[1px] shadow-sm overflow-hidden"
      style={{ left: room.x, top: room.y, width: room.w, height: room.h }}
    >
      <div className="flex items-center gap-1.5 px-3 h-[46px] border-b border-gray-100 bg-gradient-to-r from-indigo-50 to-transparent">
        <MapPin className="w-3.5 h-3.5 text-indigo-500 shrink-0" />
        <span className="text-sm font-bold text-gray-800 capitalize truncate flex-1">
          {room.name}
        </span>
        {room.occupants.length > 0 && (
          <span className="shrink-0 text-[10px] font-bold text-indigo-600 bg-indigo-50 rounded-full px-1.5">
            {room.occupants.length}
          </span>
        )}
      </div>

      {room.occupants.length === 0 && (
        <div className="px-3 pt-2 text-[11px] text-gray-300 italic">
          {t('playgroundStage.nobodyHere')}
        </div>
      )}

      {(room.items.length > 0 || room.entities.length > 0) && (
        <div className="absolute left-0 right-0 bottom-0 px-3 pb-2 space-y-1">
          {room.items.length > 0 && (
            <div className="flex gap-1 overflow-hidden">
              {room.items.slice(0, 4).map((item) => (
                <Chip key={item} icon={Package} label={item}
                      tone="bg-amber-50 text-amber-700 border-amber-200" />
              ))}
              {room.items.length > 4 && (
                <span className="text-[10px] text-gray-400 self-center">+{room.items.length - 4}</span>
              )}
            </div>
          )}
          {room.entities.length > 0 && (
            <div className="flex gap-1 overflow-hidden">
              {room.entities.slice(0, 4).map((e) => (
                <Chip key={e} icon={Box} label={e}
                      tone="bg-violet-50 text-violet-700 border-violet-200" />
              ))}
              {room.entities.length > 4 && (
                <span className="text-[10px] text-gray-400 self-center">+{room.entities.length - 4}</span>
              )}
            </div>
          )}
        </div>
      )}

      {room.cards.map((card) => (
        <AgentCard
          key={card.name} idle={idle.has(card.name)}
          card={{ ...card, x: card.x - room.x, y: card.y - room.y }}
        />
      ))}
    </div>
  );
}

/** The drawn layer: the map underneath, this tick's movement and talk on top. */
function StageLines({ layout }) {
  return (
    <svg
      className="absolute inset-0 pointer-events-none overflow-visible"
      width={layout.width} height={layout.height}
    >
      {/* The map. Dashed and pale: it says what connects to what, and it must
          never compete with what happened this tick. */}
      <g className="text-gray-300">
        {layout.edges.map((e) => (
          <line
            key={e.key} x1={e.from.x} y1={e.from.y} x2={e.to.x} y2={e.to.y}
            stroke="currentColor" strokeWidth="1.5" strokeDasharray="6 6"
          />
        ))}
      </g>

      {/* Who walked where, in the walker's own colour. Animated, because a
          still arrow between two rooms does not say which way it points until
          you find the head. */}
      {layout.moves.map((m) => {
        const { d } = arcPath(m.from, m.to, 0.16);
        const tone = toneFor(m.agent);
        const mid = { x: (m.from.x + m.to.x) / 2, y: (m.from.y + m.to.y) / 2 };
        return (
          <g key={`move-${m.agent}`} className={tone.line}>
            <path
              d={d} fill="none" stroke="currentColor" strokeWidth="2.5"
              strokeDasharray="8 6" strokeLinecap="round" opacity="0.9"
            >
              <animate attributeName="stroke-dashoffset" from="28" to="0"
                       dur="1.2s" repeatCount="indefinite" />
            </path>
            <Arrowhead at={m.to} from={m.from} size={8} />
            <LineLabel x={mid.x} y={mid.y - 6} className={tone.line}>
              {m.agent}
            </LineLabel>
          </g>
        );
      })}

      {/* Who reached for whom. A refusal is drawn too, in red and dotted: the
          world saying no is one of the more interesting things on a tick. */}
      {layout.interactions.map((i) => {
        const { d, cx, cy } = arcPath(i.from, i.to, 0.24);
        const tone = toneFor(i.agent);
        return (
          <g key={`talk-${i.key}`} className={i.refused ? 'text-red-500' : tone.line}>
            <path
              d={d} fill="none" stroke="currentColor" strokeWidth="2"
              strokeDasharray={i.refused ? '3 4' : undefined}
              strokeLinecap="round" opacity="0.85"
            />
            <Arrowhead at={i.to} from={{ x: cx, y: cy }} />
            <LineLabel x={(i.from.x + i.to.x) / 2 + (cx - (i.from.x + i.to.x) / 2) * 0.5}
                       y={(i.from.y + i.to.y) / 2 + (cy - (i.from.y + i.to.y) / 2) * 0.5}
                       className={i.refused ? 'text-red-600' : tone.line}>
              {/* The action, not the words: what was said is already in the
                  speaker's own card, and printing it twice turns a busy tick
                  into a wall of quotes. */}
              {i.action}
            </LineLabel>
          </g>
        );
      })}
    </svg>
  );
}

/** The world's own numbers, and the clock — a strip above the map. */
function StageHud({ layout, tick, t }) {
  const globals = Object.entries(layout.globals || {});
  return (
    <div className="absolute top-3 left-3 z-10 flex items-center gap-2 flex-wrap max-w-[calc(100%-1.5rem)]">
      <span className="px-2 py-1 rounded-lg bg-white/95 border border-gray-200 shadow-sm text-[11px] font-bold text-gray-700">
        {t('playgroundStage.tick', { tick })}
      </span>
      {(layout.timeOfDay || layout.hour != null) && (
        <span className="px-2 py-1 rounded-lg bg-white/95 border border-gray-200 shadow-sm text-[11px] text-gray-500">
          {layout.timeOfDay ? `${layout.timeOfDay} · ` : ''}
          {layout.hour != null ? `${layout.hour}:00` : ''}
        </span>
      )}
      {globals.map(([name, value]) => (
        <span key={name}
              className="px-2 py-1 rounded-lg bg-white/95 border border-gray-200 shadow-sm text-[11px] text-gray-500">
          {name}{' '}
          <span className="font-bold text-gray-800">{String(value)}</span>
        </span>
      ))}
    </div>
  );
}

/**
 * What the lines mean.
 *
 * The swatches copy the stroke, not the colour: movement and speech are drawn
 * in the character's own tone, so a fixed colour chip here would be a lie four
 * times out of five. Dash pattern and weight are what actually distinguish
 * them, and that is what the legend shows.
 */
function Legend({ t }) {
  const rows = [
    [<line x1="1" y1="4" x2="23" y2="4" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" />, 'text-gray-300', t('playgroundStage.legendExit')],
    [<line x1="1" y1="4" x2="23" y2="4" stroke="currentColor" strokeWidth="2.5" strokeDasharray="6 4" strokeLinecap="round" />, 'text-gray-500', t('playgroundStage.legendMove')],
    [<line x1="1" y1="4" x2="23" y2="4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />, 'text-gray-500', t('playgroundStage.legendTalk')],
    [<line x1="1" y1="4" x2="23" y2="4" stroke="currentColor" strokeWidth="2" strokeDasharray="2 3" />, 'text-red-500', t('playgroundStage.legendRefused')],
  ];
  return (
    <div className="absolute bottom-3 left-3 z-10 rounded-lg bg-white/95 border border-gray-200 shadow-sm px-2.5 py-2 space-y-1">
      {rows.map(([stroke, tone, label]) => (
        <div key={label} className="flex items-center gap-1.5 text-[10px] text-gray-500">
          <svg width="24" height="8" className={tone}>{stroke}</svg>
          {label}
        </div>
      ))}
    </div>
  );
}


/**
 * What the world wrote on this tick, beside the picture of it.
 *
 * The event log has its own tab and it is the whole run; this is only the tick
 * on screen, which is the half the canvas cannot draw: a price that moved, a
 * door that opened by itself, an action the world refused and the sentence
 * saying why. Refusals are here as well as on the card that caused them,
 * because a refusal is a fact about the world and reads as one.
 */
function TickLog({ tick }) {
  const { t } = useI18n();
  const events = tick?.events || [];
  const refused = (tick?.resolutions || []).filter((r) => !r.ok);
  const idle = tick?.idle || [];

  return (
    <aside className="hidden 2xl:flex 2xl:flex-col w-80 shrink-0 border-l border-gray-200 bg-white">
      <div className="px-3 py-2 border-b border-gray-100 text-[11px] font-bold uppercase tracking-wide text-gray-500">
        {t('playgroundStage.thisTick')}
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {events.length === 0 && refused.length === 0 && (
          <div className="text-[11px] text-gray-300 italic">{t('playgroundStage.quietTick')}</div>
        )}
        {events.map((text, i) => (
          <div key={`e${i}`} className="text-[11px] text-gray-600 leading-snug">
            <span className="text-gray-300">•</span> {text}
          </div>
        ))}
        {refused.map((r, i) => (
          <div key={`r${i}`} className="text-[11px] text-red-600 leading-snug">
            <span className={`font-semibold ${toneFor(r.agent).name}`}>{r.agent}</span>{' '}
            <span className="font-semibold">{r.action}</span>: {r.message}
          </div>
        ))}
      </div>
      {idle.length > 0 && (
        <div className="px-3 py-2 border-t border-gray-100 text-[10px] text-gray-400">
          {t('playgroundStage.silent', { names: idle.join(', ') })}
        </div>
      )}
    </aside>
  );
}


/**
 * The stage.
 *
 * Everything it draws comes from the tick the scrubber is on, so the transport
 * bar in the page header doubles as this view's playback: scrubbing back a tick
 * moves everybody back to where they stood, arrows and all.
 */
export default function StageCanvas({
  ticks = [], cursor = 0, roles = [], activity = [], following = false,
  className = '',
}) {
  const { t } = useI18n();
  const tick = ticks[cursor];
  const frame = tick?.frame;
  const wrapRef = useRef(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const dragRef = useRef(null);
  // Fit once per world shape rather than on every tick: a canvas that re-centres
  // itself while you are reading it is unusable, but landing on a map scaled to
  // nothing is worse.
  const fittedRef = useRef('');

  const layout = useMemo(() => stageLayout({
    frame,
    prevFrame: cursor > 0 ? ticks[cursor - 1]?.frame : null,
    tick,
    // Only when the scrubber is on the newest tick: a half-finished turn drawn
    // over tick 3 of 40 would be a lie about tick 3.
    activity: following && cursor === ticks.length - 1 ? activity : [],
    roles,
  }), [frame, tick, ticks, cursor, activity, following, roles]);

  const idle = useMemo(() => new Set(tick?.idle || []), [tick]);

  const fit = useCallback(() => {
    const box = wrapRef.current?.getBoundingClientRect();
    if (!box || !layout.width || !layout.height) return;
    const k = Math.min(
      MAX_ZOOM,
      Math.max(MIN_ZOOM, Math.min(box.width / layout.width, box.height / layout.height)),
    );
    setView({
      k,
      x: (box.width - layout.width * k) / 2,
      y: Math.max(0, (box.height - layout.height * k) / 2),
    });
  }, [layout.width, layout.height]);

  useEffect(() => {
    const shape = `${layout.rooms.map((r) => r.name).join('|')}:${Math.round(layout.height)}`;
    if (shape === fittedRef.current || !layout.rooms.length) return undefined;
    fittedRef.current = shape;
    // After the paint that laid the canvas out, not during it: fitting reads
    // the container's size, and on the first render there is nothing to read.
    const frameId = requestAnimationFrame(fit);
    return () => cancelAnimationFrame(frameId);
  }, [layout, fit]);

  const onPointerDown = (e) => {
    if (e.button !== 0) return;
    dragRef.current = { x: e.clientX, y: e.clientY, view };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e) => {
    const drag = dragRef.current;
    if (!drag) return;
    setView({
      ...drag.view,
      x: drag.view.x + (e.clientX - drag.x),
      y: drag.view.y + (e.clientY - drag.y),
    });
  };
  const onPointerUp = () => { dragRef.current = null; };

  // Zoom about the pointer, so the thing under the cursor stays under it.
  // Bound by hand rather than through onWheel: React registers wheel handlers
  // passively, and a zoom that also scrolls the page under it is unusable.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const box = el.getBoundingClientRect();
      const px = e.clientX - box.left;
      const py = e.clientY - box.top;
      setView((v) => {
        const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.k * (e.deltaY < 0 ? 1.12 : 1 / 1.12)));
        const ratio = k / v.k;
        return { k, x: px - (px - v.x) * ratio, y: py - (py - v.y) * ratio };
      });
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, []);

  const zoomBy = (factor) => setView((v) => {
    const box = wrapRef.current?.getBoundingClientRect();
    const px = (box?.width || 0) / 2;
    const py = (box?.height || 0) / 2;
    const k = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.k * factor));
    const ratio = k / v.k;
    return { k, x: px - (px - v.x) * ratio, y: py - (py - v.y) * ratio };
  });

  if (!tick) {
    return (
      <div className={`grid place-items-center border-t border-gray-200 rounded-b-xl bg-gray-50 text-sm text-gray-400 italic ${className}`}>
        {t('playgroundStage.nothingToShow')}
      </div>
    );
  }

  // A world without rooms — the market is the shipped case — has no map to
  // draw. Rather than an empty canvas, it gets the reading it does have.
  if (!layout.rooms.length) {
    return (
      <div className={`overflow-y-auto border-t border-gray-200 rounded-b-xl bg-white p-5 ${className}`}>
        <p className="text-xs text-gray-400 italic mb-3">{t('playgroundStage.noMap')}</p>
        <WorldView frame={frame} />
      </div>
    );
  }

  return (
    /* One block with a rule down it, not two cards with air between them: the
       map and the world's own lines are the same tick, and the card around
       them is already a frame. It draws only its top border — the card owns
       the other three — and rounds its floor to sit inside the card's. */
    <div className={`flex border-t border-gray-200 rounded-b-xl overflow-hidden ${className}`}>
      <div className="relative flex-1 min-w-0">
        <div
          ref={wrapRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          className="absolute inset-0 overflow-hidden bg-gray-50 cursor-grab active:cursor-grabbing"
          style={{
            // Through the border token rather than a black wash: the grid has
            // to read as faint on both surfaces, and black on a dark page is
            // not faint, it is invisible.
            backgroundImage:
              'radial-gradient(circle at 1px 1px, var(--border-strong) 1px, transparent 0)',
            backgroundSize: '24px 24px',
          }}
        >
          <div
            className="absolute top-0 left-0 origin-top-left"
            style={{
              width: layout.width, height: layout.height,
              transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
            }}
          >
            {layout.rooms.map((room) => (
              <RoomCard key={room.name} room={room} idle={idle} />
            ))}
            {/* Over the cards, not under them: a line that ends at an avatar has
                its arrowhead — the half that says which way it points — exactly
                where a card would hide it. Labels carry a halo in the card
                colour, so crossing one stays readable. */}
            <StageLines layout={layout} />
          </div>
        </div>

        <StageHud layout={layout} tick={tick.tick} t={t} />
        <Legend t={t} />

        <div className="absolute top-3 right-3 z-10 flex items-center gap-1 rounded-lg bg-white/95 border border-gray-200 shadow-sm p-1">
          <button onClick={() => zoomBy(1 / 1.2)} title={t('playgroundStage.zoomOut')}
                  className="p-1 text-gray-500 hover:text-gray-800">
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className="text-[10px] font-semibold text-gray-400 w-8 text-center">
            {Math.round(view.k * 100)}%
          </span>
          <button onClick={() => zoomBy(1.2)} title={t('playgroundStage.zoomIn')}
                  className="p-1 text-gray-500 hover:text-gray-800">
            <ZoomIn className="w-4 h-4" />
          </button>
          <button onClick={fit} title={t('playgroundStage.fit')}
                  className="p-1 text-gray-500 hover:text-gray-800 border-l border-gray-200 ml-0.5 pl-1.5">
            <Maximize2 className="w-4 h-4" />
          </button>
        </div>

        {layout.homeless.length > 0 && (
          <div className="absolute bottom-3 right-3 z-10 rounded-lg bg-amber-50 border border-amber-200 px-2 py-1 text-[10px] text-amber-700">
            {t('playgroundStage.offMap', {
              names: layout.homeless.map((a) => a.name).join(', '),
            })}
          </div>
        )}
      </div>

      {/* Beside the map rather than over it: the world's own lines are the one
          thing on a tick the picture cannot show. Dropped on narrow screens,
          where the map needs every pixel and the events have their own tab. */}
      <TickLog tick={tick} />
    </div>
  );
}
