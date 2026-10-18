/**
 * The speech model's voice per language (providers/speech_languages.py): for
 * each page language, another model of the same provider and/or another
 * voice. A line is read with the entry of
 * its own language; an empty row keeps the model's own voice.
 */
import { LANGUAGES, useI18n } from '../../i18n';
import { inputCls } from '../settingsUi';

export default function SpeechLanguagesField({ value, onChange, model, voice }) {
  const { t } = useI18n();
  const rows = value || {};
  const codes = [...LANGUAGES.map((l) => l.code), ...Object.keys(rows).filter((c) => !LANGUAGES.some((l) => l.code === c))];

  const set = (code, patch) => {
    const next = { ...rows, [code]: { ...(rows[code] || {}), ...patch } };
    const row = next[code];
    if (!String(row.model || '').trim() && !String(row.voice || '').trim()) delete next[code];
    onChange(next);
  };

  return (
    <div className="space-y-2" data-testid="voice-speech-languages">
      <p className="text-xs font-medium text-gray-600">{t('models.voice.languages.title')}</p>
      <p className="text-xs text-gray-500">{t('models.voice.languages.hint')}</p>
      {codes.map((code) => {
        const label = LANGUAGES.find((l) => l.code === code)?.label || code;
        return (
          <div key={code} className="grid grid-cols-1 gap-2 md:grid-cols-[8rem_1fr_1fr] md:items-center">
            <span className="text-xs font-medium text-gray-700">{label}</span>
            <input
              value={rows[code]?.model || ''}
              placeholder={model || t('models.voice.languages.model')}
              aria-label={t('models.voice.languages.modelFor', { language: label })}
              onChange={(e) => set(code, { model: e.target.value })}
              className={inputCls}
              data-testid={`voice-speech-language-${code}-model`}
            />
            <input
              value={rows[code]?.voice || ''}
              placeholder={voice || t('models.voice.languages.voice')}
              aria-label={t('models.voice.languages.voiceFor', { language: label })}
              onChange={(e) => set(code, { voice: e.target.value })}
              className={inputCls}
              data-testid={`voice-speech-language-${code}-voice`}
            />
          </div>
        );
      })}
    </div>
  );
}
