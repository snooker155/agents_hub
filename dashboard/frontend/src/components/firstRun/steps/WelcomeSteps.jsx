/**
 * The first three screens: hello, the language, light or dark. Each choice
 * applies the moment it is made, so the next screen is already in it.
 */
import { useEffect, useState } from 'react';
import { Languages, Monitor, Moon, Palette, Sun } from 'lucide-react';
import LiveMark from '../../liveMark/LiveMark';
import { LANGUAGES, useI18n } from '../../../i18n';
import { useTheme } from '../../theme';
import { ChoiceCard, PrimaryButton, StepFrame } from '../ui';

// Hello in each language the hub speaks, cycled under the mark.
const HELLOS = ['Hello', 'Привет', 'Hallo'];
const HELLO_MS = 2200;

export function HelloStep({ next }) {
  const { t } = useI18n();
  const [i, setI] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setI((n) => (n + 1) % HELLOS.length), HELLO_MS);
    return () => clearInterval(id);
  }, []);
  return (
    <section className="flex-1 flex flex-col items-center justify-center text-center" data-testid="first-run-hello">
      <LiveMark state="speak" size={112} frame="logo" label="Agents Hub" />
      <h1 key={i} className="mt-10 text-5xl sm:text-6xl font-semibold tracking-tight text-gray-900 first-run-fade">
        {HELLOS[i]}
      </h1>
      <p className="mt-4 text-lg text-gray-500 max-w-sm">{t('firstRun.hello.subtitle')}</p>
      <div className="mt-12 w-full flex justify-center">
        <PrimaryButton onClick={next} testId="first-run-start">{t('firstRun.hello.start')}</PrimaryButton>
      </div>
    </section>
  );
}

export function LanguageStep({ next }) {
  const { t, language, setLanguage } = useI18n();
  return (
    <StepFrame
      icon={Languages}
      title={t('firstRun.language.title')}
      subtitle={t('firstRun.language.subtitle')}
      testId="first-run-language"
      footer={<PrimaryButton onClick={() => next({ language })} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>}
    >
      <div className="space-y-2">
        {LANGUAGES.map((l) => (
          <ChoiceCard
            key={l.code}
            title={`${l.flag}  ${l.label}`}
            selected={language === l.code}
            onClick={() => setLanguage(l.code)}
            testId={`first-run-language-${l.code}`}
          />
        ))}
      </div>
    </StepFrame>
  );
}

/** A tiny window in the given look, for the appearance cards. */
function Preview({ mode }) {
  const pane = (dark) => (
    <div className={`flex-1 h-full p-2 flex gap-1.5 ${dark ? 'bg-[#0f1729]' : 'bg-white'}`}>
      <div className={`w-1/4 rounded ${dark ? 'bg-[#1b2740]' : 'bg-gray-100'}`} />
      <div className="flex-1 flex flex-col gap-1">
        <div className={`h-2 rounded ${dark ? 'bg-[#2a3a5c]' : 'bg-gray-200'}`} />
        <div className={`h-2 w-2/3 rounded ${dark ? 'bg-[#2a3a5c]' : 'bg-gray-200'}`} />
        <div className="h-2 w-1/3 rounded bg-[#3f66d8]" />
      </div>
    </div>
  );
  return (
    <div className="mt-3 h-20 w-full rounded-lg overflow-hidden border border-gray-200 flex">
      {mode === 'system' ? <>{pane(false)}{pane(true)}</> : pane(mode === 'dark')}
    </div>
  );
}

const LOOKS = [
  { id: 'light', icon: Sun },
  { id: 'dark', icon: Moon },
  { id: 'system', icon: Monitor },
];

export function AppearanceStep({ next }) {
  const { t } = useI18n();
  const { theme, setTheme } = useTheme();
  return (
    <StepFrame
      icon={Palette}
      title={t('firstRun.appearance.title')}
      subtitle={t('firstRun.appearance.subtitle')}
      testId="first-run-appearance"
      footer={<PrimaryButton onClick={() => next({ theme })} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>}
    >
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {LOOKS.map(({ id, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setTheme(id)}
            aria-pressed={theme === id}
            data-testid={`first-run-look-${id}`}
            className={`rounded-2xl border p-3 text-left transition-colors ${
              theme === id ? 'border-indigo-500 ring-1 ring-indigo-500 bg-indigo-50/60' : 'border-gray-200 bg-white hover:border-gray-300'
            }`}
          >
            <span className="flex items-center gap-2 text-sm font-semibold text-gray-900">
              <Icon className="w-4 h-4" />{t(`firstRun.appearance.${id}`)}
            </span>
            <Preview mode={id} />
          </button>
        ))}
      </div>
      <p className="mt-4 text-sm text-center text-gray-400">{t('firstRun.appearance.later')}</p>
    </StepFrame>
  );
}
