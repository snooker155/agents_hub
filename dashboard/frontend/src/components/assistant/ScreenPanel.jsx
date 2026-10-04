/**
 * "Show on screen": a page of the dashboard opened beside the assistant
 * instead of in place of it, so the conversation (and the voice) keep going.
 *
 * The page is the app itself in a frame named `ah-embed`, which Layout reads
 * as "draw the page alone, without the sidebar and the header"
 * (components/embed.js). The frame keeps that name while the person follows
 * links inside it, so every page they reach there is drawn the same way.
 */
import { ExternalLink, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useI18n } from '../../i18n';
import { EMBED_FRAME_NAME, embedUrl } from '../embed';

export default function ScreenPanel({ path, onClose }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  if (!path) return null;
  return (
    <div className="flex flex-col min-h-0 h-full" data-testid="assistant-screen">
      <div className="flex items-center gap-2 px-3 h-11 border-b border-gray-200 shrink-0">
        <code className="text-xs text-gray-500 truncate flex-1" title={path}>{path}</code>
        <button
          type="button" onClick={() => navigate(path)}
          title={t('assistant.screen.open')} aria-label={t('assistant.screen.open')}
          className="p-1.5 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100"
        >
          <ExternalLink className="w-4 h-4" />
        </button>
        <button
          type="button" onClick={onClose}
          title={t('assistant.screen.close')} aria-label={t('assistant.screen.close')}
          className="p-1.5 rounded text-gray-400 hover:text-gray-700 hover:bg-gray-100"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      <iframe
        key={path}
        name={EMBED_FRAME_NAME}
        title={t('assistant.screen.title')}
        src={embedUrl(path)}
        className="flex-1 w-full min-h-0 border-0 bg-white"
      />
    </div>
  );
}
