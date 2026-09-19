/**
 * The run, read as a place rather than as a list.
 *
 * Everything drawn on the stage is derived here, and deliberately: the canvas
 * has to know where a thing *is* before it can draw a line to it, so the layout
 * is arithmetic over the frame rather than a side effect of how the browser
 * happened to flow some boxes. One pure function in, one object of coordinates
 * out — which is also what makes the whole thing testable without a DOM.
 *
 * What it reads, and why each part is needed:
 *   * `frame.locations` — the rooms, their occupants, what lies on the floor
 *     and which fixtures stand there. `exits` (authored worlds declare it) is
 *     the map: with it the rooms are drawn as the shape the world actually is.
 *   * `frame.agents` — where each character is, what it carries, its stats.
 *   * `tick.decisions` — what each character did *this* tick, which is the
 *     layer the sidebar's world view never had: a list of positions says where
 *     everyone stands, not what they are doing.
 *   * `tick.resolutions` — what the world made of it, including the `from`/`to`
 *     of a move, which is what a movement arrow is drawn from.
 *
 * Movement is taken from the resolution when the world reported one and from
 * the previous frame otherwise, because an authored action can walk somebody
 * across the map without ever being called `move_to`.
 */

import { ADDRESSEE_ARGS, SPEECH_ARGS, addresseeOf, speechOf } from './cast';

// ── geometry ─────────────────────────────────────────────────────────────────
// One place for every number the canvas is built out of. They are constants
// rather than measurements because the arrows between two agents have to be
// drawn in the same pass as the cards they connect: a layout the browser owns
// could only be measured after it had already been painted.
export const ROOM_W = 300;
export const ROOM_GAP_X = 72;
export const ROOM_GAP_Y = 64;
export const ROOM_HEAD = 46;      // the location's name row
export const ROOM_PAD = 12;
export const CARD_GAP = 8;
export const CARD_BASE = 40;      // avatar + name, before anything optional
export const CARD_LINE = 20;      // one extra row (action, items, stats…)
export const CHIP_ROW = 24;       // a row of item/fixture chips in the footer
// The avatar's centre inside its card — the point every arrow starts and ends
// at. It is the card's own padding plus half an avatar, written down rather
// than measured for the same reason as everything else here.
export const AVATAR_DX = 22;
export const AVATAR_DY = 20;
export const MARGIN = 40;         // breathing room around the whole map

/** How many rooms stand side by side. Squarish, and never so wide that a
    fifteen-room world becomes one unreadable ribbon. */
export function columnsFor(count) {
  if (count <= 1) return 1;
  if (count <= 4) return 2;
  return Math.min(4, Math.ceil(Math.sqrt(count)));
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

/** The action an agent submitted this tick, in the one shape the stage draws. */
function readAction(decision) {
  const action = decision?.action;
  if (!action) return null;
  const name = String(action.action || action.name || '').trim();
  if (!name) return null;
  const args = action.args || {};
  const speech = speechOf(action);
  const to = addresseeOf(action);
  const rest = Object.entries(args)
    .filter(([k]) => !SPEECH_ARGS.includes(k) && !ADDRESSEE_ARGS.includes(k))
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(', ');
  return { name, args, to, speech: speech?.text || '', rest };
}

/**
 * Everything one tick did, keyed by the agent that did it.
 *
 * Resolutions are folded in here rather than looked up at draw time so that a
 * refusal — the world saying no — travels with the action it refused: an agent
 * that tried to walk through a wall should not be drawn as if it had walked.
 */
function turnsOf(tick, activity) {
  const turns = new Map();
  const ensure = (agent) => {
    if (!turns.has(agent)) {
      turns.set(agent, { agent, action: null, results: [], thinking: false, error: '' });
    }
    return turns.get(agent);
  };
  asArray(tick?.decisions).forEach((d) => {
    const turn = ensure(d.agent);
    turn.action = readAction(d);
    turn.error = d.error || '';
    turn.reasoning = d.reasoning || '';
    turn.run_id = d.run_id || null;
  });
  asArray(tick?.resolutions).forEach((r) => {
    ensure(r.agent).results.push(r);
  });
  // Agents woken and still writing. Only ever additive: a tick that has been
  // written is the truth, and this is the half-second before it is.
  asArray(activity).forEach((a) => {
    const turn = ensure(a.agent);
    turn.thinking = true;
    if (!turn.action && a.action) {
      turn.action = readAction({ action: { action: a.action, args: {} } });
    }
  });
  return turns;
}

/** Where each agent stood before this tick — the other end of a move arrow. */
function previousPlaces(prevFrame) {
  const places = new Map();
  asArray(prevFrame?.agents).forEach((a) => places.set(a.name, a.location));
  return places;
}

/**
 * The stage, as coordinates.
 *
 * @param {object} args
 * @param {object} args.frame     the tick's world frame
 * @param {object} args.prevFrame the frame before it, for movement
 * @param {object} args.tick      the tick record (decisions, resolutions)
 * @param {Array}  args.activity  agents mid-turn, when the cursor is live
 * @param {Array}  args.roles     the scenario's cast, for role labels
 */
export function stageLayout({ frame, prevFrame, tick, activity = [], roles = [] } = {}) {
  const locations = asArray(frame?.locations);
  const agents = asArray(frame?.agents);
  const turns = turnsOf(tick, activity);
  const before = previousPlaces(prevFrame);
  const roleOf = new Map(
    asArray(roles).map((r) => [r.display_name || r.name || r.agent_id, r]),
  );

  // Agents the frame knows about but no location claims — an environment that
  // reports positions loosely should still show its cast rather than swallow it.
  const byLocation = new Map(locations.map((l) => [l.name, []]));
  const agentByName = new Map(agents.map((a) => [a.name, a]));
  const placed = new Set();
  locations.forEach((loc) => {
    asArray(loc.occupants).forEach((name) => {
      if (agentByName.has(name) || name) {
        byLocation.get(loc.name).push(name);
        placed.add(name);
      }
    });
  });
  agents.forEach((a) => {
    if (placed.has(a.name)) return;
    if (byLocation.has(a.location)) {
      byLocation.get(a.location).push(a.name);
      placed.add(a.name);
    }
  });

  // ── heights, then positions ────────────────────────────────────────────────
  const cardHeight = (name) => {
    const a = agentByName.get(name) || {};
    const turn = turns.get(name);
    let h = CARD_BASE;
    if (turn?.action || turn?.thinking) h += CARD_LINE;
    if (turn?.action?.speech) h += CARD_LINE;
    if (asArray(a.inventory).length) h += CARD_LINE;
    if (Object.keys(a.stats || {}).length) h += CARD_LINE;
    return h;
  };

  const rooms = locations.map((loc) => {
    const occupants = byLocation.get(loc.name) || [];
    const heights = occupants.map(cardHeight);
    const body = heights.reduce((sum, h) => sum + h, 0)
      + Math.max(0, occupants.length - 1) * CARD_GAP;
    const footer = (asArray(loc.items).length ? CHIP_ROW : 0)
      + (asArray(loc.entities).length ? CHIP_ROW : 0);
    const empty = occupants.length ? 0 : CARD_LINE;
    return {
      name: loc.name,
      items: asArray(loc.items),
      entities: asArray(loc.entities),
      exits: asArray(loc.exits),
      occupants,
      heights,
      h: ROOM_HEAD + ROOM_PAD + body + empty + (footer ? footer + 6 : 0) + ROOM_PAD,
      w: ROOM_W,
    };
  });

  const cols = columnsFor(rooms.length);
  const rowHeights = [];
  rooms.forEach((room, i) => {
    const row = Math.floor(i / cols);
    rowHeights[row] = Math.max(rowHeights[row] || 0, room.h);
  });
  const rowTop = [];
  rowHeights.reduce((y, h, i) => {
    rowTop[i] = y;
    return y + h + ROOM_GAP_Y;
  }, MARGIN);

  const anchors = new Map();   // agent → the centre of its avatar
  rooms.forEach((room, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    room.x = MARGIN + col * (ROOM_W + ROOM_GAP_X);
    room.y = rowTop[row];
    let y = room.y + ROOM_HEAD + ROOM_PAD;
    room.cards = room.occupants.map((name, j) => {
      const card = {
        name,
        x: room.x + ROOM_PAD,
        y,
        w: ROOM_W - 2 * ROOM_PAD,
        h: room.heights[j],
        agent: agentByName.get(name) || { name, location: room.name },
        role: roleOf.get(name) || null,
        turn: turns.get(name) || null,
      };
      anchors.set(name, { x: card.x + AVATAR_DX, y: card.y + AVATAR_DY, room });
      y += card.h + CARD_GAP;
      return card;
    });
  });

  const roomByName = new Map(rooms.map((r) => [r.name, r]));
  const centre = (room) => ({ x: room.x + room.w / 2, y: room.y + room.h / 2 });

  // ── the map: every declared exit, once ─────────────────────────────────────
  const seen = new Set();
  const edges = [];
  rooms.forEach((room) => {
    room.exits.forEach((exit) => {
      const other = roomByName.get(exit);
      if (!other || other === room) return;
      const key = [room.name, exit].sort().join(' ');
      if (seen.has(key)) return;
      seen.add(key);
      edges.push({ key, from: centre(room), to: centre(other) });
    });
  });

  // ── who went where ─────────────────────────────────────────────────────────
  const moves = [];
  agents.forEach((a) => {
    const turn = turns.get(a.name);
    const walked = asArray(turn?.results).find(
      (r) => r.ok && r.effects && r.effects.to && r.effects.to !== r.effects.from,
    );
    const from = walked?.effects?.from ?? before.get(a.name);
    const to = walked?.effects?.to ?? a.location;
    if (!from || !to || from === to) return;
    const a1 = roomByName.get(from);
    const a2 = roomByName.get(to);
    if (!a1 || !a2) return;
    moves.push({
      agent: a.name,
      from: centre(a1), to: anchors.get(a.name) || centre(a2),
      fromName: from, toName: to,
    });
  });

  // ── who reached for whom ───────────────────────────────────────────────────
  // Only where both ends are on the stage: an action aimed at somebody who is
  // not in this frame has nothing to draw, and a line to nowhere reads as a bug.
  const interactions = [];
  turns.forEach((turn, name) => {
    const to = turn.action?.to;
    if (!to || to === name) return;
    const from = anchors.get(name);
    const target = anchors.get(to);
    if (!from || !target) return;
    const result = turn.results.find((r) => r.action === turn.action.name);
    interactions.push({
      key: `${name} ${to}`,
      agent: name, target: to,
      action: turn.action.name,
      speech: turn.action.speech,
      refused: result ? !result.ok : false,
      from, to: target,
    });
  });

  const width = MARGIN * 2 + cols * ROOM_W + (cols - 1) * ROOM_GAP_X;
  const height = rooms.length
    ? rowTop[rowTop.length - 1] + rowHeights[rowHeights.length - 1] + MARGIN
    : 0;

  return {
    rooms, edges, moves, interactions, anchors, turns,
    width: Math.max(width, 0), height: Math.max(height, 0),
    // Characters the frame reports with no room to stand in. Rare, and worth
    // showing rather than dropping: it is usually a world bug.
    homeless: agents.filter((a) => !placed.has(a.name)),
    globals: frame?.globals || {},
    hour: frame?.hour, timeOfDay: frame?.time_of_day,
  };
}

export default stageLayout;
