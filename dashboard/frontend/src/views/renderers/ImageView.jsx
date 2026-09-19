import React from 'react';
import { viewAssetUrl } from '../../api';
import { useI18n } from '../../i18n';

// Image renderer — resolves a bare filename to a view-asset URL, or passes an
// absolute/data URL straight through.

function resolveSrc(view) {
  const src = view?.spec?.src || '';
  if (/^(https?:|data:|\/)/i.test(src)) return src;
  if (view?.view_id) return viewAssetUrl(view.view_id, src);
  return src;
}

export default function ImageView({ view }) {
  const { t } = useI18n();
  const src = resolveSrc(view);
  const caption = view?.spec?.caption || '';
  if (!src) return <div className="text-sm text-gray-500">{t('viewImageView.missingSrc')}</div>;
  return (
    <figure className="m-0">
      <img src={src} alt={caption || view?.title || 'view image'} className="max-w-full rounded-lg border border-gray-200 dark:border-gray-700" />
      {caption && <figcaption className="mt-1 text-xs text-gray-500 text-center">{caption}</figcaption>}
    </figure>
  );
}
