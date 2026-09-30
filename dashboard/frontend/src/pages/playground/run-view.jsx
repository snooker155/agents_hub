import React from 'react';
import { BookOpen, BookText, ListTree, Map, MessagesSquare } from 'lucide-react';
import { useI18n } from '../../i18n';
import { PaneTab, ScenarioSidebar } from './scenario-sidebar';
import StageCanvas from './stage';
import NarrativePanel from './narrative';
import StoryPane from './story';
import { ScenarioTranscript, EventFeed } from './transcript';
import { TriggerComposer } from './trigger-composer';

// Setting up and watching stand the same sidebar beside their main column, so
// the template lives here rather than being written out at both call sites —
// where it had already drifted by 2rem, which read as the page resizing itself
// when you switched tabs.
export const TWO_COLUMNS = 'grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_26rem] gap-6';

// The run's feed owns whatever height is left under the transport bar once the
// layout is a column (xl and up); narrower than that the page is an ordinary
// scroll, so the feed falls back to a bounded box.
const FEED_HEIGHT = 'max-h-[calc(100vh-24rem)] min-h-[18rem] xl:flex-1 xl:min-h-0 xl:max-h-none';

// The map and the narrative editor are not feeds: they do not scroll a list,
// they fill what they are given. A taller floor than a feed's, because a map
// scaled into eighteen rems is a diagram of nothing.
const PANE_HEIGHT = 'min-h-[30rem] xl:flex-1 xl:min-h-0';

/**
 * Watching a run: the two columns behind the mode switch — the run itself
 * (narrative, stage, transcript, events, story, behind one switch), and the
 * sidebar with the world's readouts and the build chat.
 *
 * The five readings of one run share the column rather than competing for the
 * eye side by side; see ``PlaygroundScenario.jsx`` for why watching is not a
 * Studio tab.
 */
export function RunView({
  pane, setPane, ticks, cursor, scenario, activity, inFlight, following, run, roleNames,
  onTrigger, live, waitingForTrigger, eventCount, eventsSeen, onAskAgent, onScenarioSaved,
  activation, currentTick, sideTab, onSideTab, envSpec, chat,
}) {
  const { t } = useI18n();
  return (
    <div className={`${TWO_COLUMNS} xl:flex-1 xl:min-h-0`}>
      {/* The run as a conversation: every turn since tick 0, each with
          the agent's thought and its call on the environment — the same
          three things a chat with an agent shows. Behind the second tab,
          the same ticks as the world wrote them. */}
      {/* Less padding above the switcher than around the rest: it is the
          card's own title row, and a full gutter over it pushed the
          transcript down for nothing. No padding under the feed either:
          the scroll runs to the card's edge, so a long transcript reads
          as continuing past the border instead of stopping short of it.
          The composer carries that gutter itself when it is there. */}
      <div className={`bg-white rounded-xl border border-gray-200 px-5 pt-3 shadow-sm min-w-0 flex flex-col xl:min-h-0 ${
        /* A feed runs to the card's bottom edge on purpose, and so does
           the map — it takes the whole card under the switch, sides and
           floor included, because a map inset in a gutter is a map with
           less map in it. A page being written keeps the gutter. */
        pane === 'narrative' ? 'pb-5' : 'pb-0'
      }`}>
        <div className="shrink-0 flex items-center justify-between gap-2 mb-3 flex-wrap">
          <div className="inline-flex items-center gap-1 p-0.5 rounded-lg bg-gray-100">
            {/* The world first and the place it happens in second: both
                are what the run is *of*, and they are read before and
                around it rather than after it. The three readings of the
                run itself follow, transcript first — it is what you came
                for, and it is what the card opens on. */}
            <PaneTab
              active={pane === 'narrative'}
              onClick={() => setPane('narrative')}
              icon={BookText}
              label={t('playgroundNarrative.tab')}
            />
            <PaneTab
              active={pane === 'stage'}
              onClick={() => setPane('stage')}
              icon={Map}
              label={t('playground.stageTitle')}
            />
            <PaneTab
              active={pane === 'transcript'}
              onClick={() => setPane('transcript')}
              icon={MessagesSquare}
              label={t('playground.transcriptTitle')}
            />
            <PaneTab
              active={pane === 'events'}
              onClick={() => setPane('events')}
              icon={ListTree}
              label={t('playground.events')}
              /* Unread rather than total: the point of the badge is that
                 something happened while you were reading the dialogue,
                 and a running total says that on every tick. */
              badge={pane === 'events' ? 0 : Math.max(0, eventCount - eventsSeen)}
            />
            {/* The same run with the seams taken out: every turn and
                every event woven into one text, which is the reading
                neither list can give. Last of the three because it is
                what you turn to once the run has something to say. */}
            <PaneTab
              active={pane === 'story'}
              onClick={() => setPane('story')}
              icon={BookOpen}
              label={t('playground.storyTitle')}
            />
          </div>
          <span className="text-[11px] text-gray-400">
            {t(`playground.activation.${activation}`)}
            {currentTick ? ` · ${t('playground.tickLabelShort', { tick: currentTick.tick })}` : ''}
          </span>
        </div>

        {pane === 'stage' ? (
          <StageCanvas
            ticks={ticks}
            cursor={cursor}
            roles={scenario.roles || []}
            activity={activity}
            following={following}
            /* Out past the card's own padding, to its border on three
               sides: the negative margins are what make the map the
               card's floor rather than a panel floating inside it. */
            className={`${PANE_HEIGHT} -mx-5`}
          />
        ) : pane === 'narrative' ? (
          <NarrativePanel
            scenario={scenario}
            onSaved={onScenarioSaved}
            /* Writing a world is exactly the job the build chat is for,
               and it already has the tool that stores this field — so the
               pane offers it rather than leaving the user to find the
               chat and phrase the request. */
            onAskAgent={onAskAgent}
            className={PANE_HEIGHT}
          />
        ) : pane === 'story' ? (
          <StoryPane
            runId={run?.sim_run_id}
            status={run?.status}
            /* Recomposed as the run grows, and only while this pane is
               the one on screen — it is mounted by the switch. */
            ticksDone={ticks.length}
            heightClass={FEED_HEIGHT}
          />
        ) : pane === 'transcript' ? (
          <>
            <ScenarioTranscript
              ticks={ticks}
              cursor={cursor}
              activation={activation}
              inFlight={inFlight}
              activity={activity}
              following={following}
              waitingForTrigger={waitingForTrigger}
              status={run?.status}
              heightClass={FEED_HEIGHT}
            />

            {live && (
              /* Pinned to the bottom of the card, and its rule drawn
                 across the whole of it: the composer is the card's
                 floor, the way the chat's is, not a block that floats
                 wherever the transcript happens to end. The negative
                 margin is what takes the border out to the card's
                 edges past its padding. */
              <TriggerComposer
                agents={roleNames}
                onSend={onTrigger}
                className="shrink-0 mt-auto -mx-5 px-5 pt-3 pb-5 border-t border-gray-200"
              />
            )}
          </>
        ) : (
          <EventFeed ticks={ticks} cursor={cursor} heightClass={FEED_HEIGHT} />
        )}
      </div>

      <ScenarioSidebar
        tab={sideTab} onTab={onSideTab}
        scenario={scenario} envSpec={envSpec} currentTick={currentTick}
        run={run} ticks={ticks} live={live}
        chat={chat}
      />
    </div>
  );
}

export default RunView;
