import React, { useState } from 'react';
import FitText from './FitText';
import SlideMarkdown from './SlideMarkdown';
import { plainText } from './markdown';
import { FONT, STAGE_H, STAGE_W, deckTheme } from './deck';

// One slide on the fixed 1280 x 720 stage the .pptx export draws on
// (views/slides_pptx.py), in the same palette and the same layouts. The host
// scales the stage to fit. Styles are inline so the PDF export can copy the
// HTML into a print window.

function Header({ s, t, accent, dark = false }) {
  const title = s.title || '';
  const color = dark ? t.heroText : t.text;
  return (
    <div style={{ flex: '0 0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 20, minHeight: 64 }}>
        {s.icon && <span style={{ fontSize: 40, lineHeight: 1 }}>{s.icon}</span>}
        <div style={{ fontSize: title.length <= 48 ? 40 : 32, fontWeight: 700, color, lineHeight: 1.15 }}>{title}</div>
      </div>
      <div style={{ width: 64, height: 6, borderRadius: 3, background: accent, margin: '12px 0 18px' }} />
      {s.subtitle && <div style={{ fontSize: 22, color: dark ? t.heroMuted : t.muted, marginBottom: 12 }}>{s.subtitle}</div>}
    </div>
  );
}

function Footer({ spec, t, number, total, dark = false, left = 80, right = 80 }) {
  const color = dark ? t.heroMuted : t.muted;
  const showNumber = spec?.numbers !== false;
  if (!spec?.footer && !showNumber) return null;
  return (
    <div style={{ position: 'absolute', left, right, bottom: 20, display: 'flex', justifyContent: 'space-between', fontSize: 13, color }}>
      <span>{spec?.footer || ''}</span>
      {showNumber && <span>{number} / {total}</span>}
    </div>
  );
}

function Body({ md, t, accent, base = 26, min = 13, color, muted }) {
  if (!md) return <div style={{ flex: 1 }} />;
  return (
    <FitText base={base} min={min} contentKey={md} style={{ flex: 1 }}>
      <SlideMarkdown md={md} t={t} accent={accent} color={color} muted={muted} />
    </FitText>
  );
}

function SlideImage({ src, alt, t }) {
  const [failed, setFailed] = useState('');
  if (!src || failed === src) {
    return <div style={{ width: '100%', height: '100%', background: t.surface, display: 'flex', alignItems: 'center', justifyContent: 'center', color: t.muted, fontSize: 40 }}>🖼</div>;
  }
  return <img src={src} alt={alt || ''} onError={() => setFailed(src)} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />;
}

// Reset what a host element could pass down (a thumbnail sits inside a
// <button>, which centres text), so a slide looks the same wherever it is drawn.
const base = (t) => ({
  position: 'relative', width: STAGE_W, height: STAGE_H, overflow: 'hidden',
  background: t.bg, color: t.text, fontFamily: FONT, boxSizing: 'border-box',
  textAlign: 'left', fontSize: 16, fontWeight: 400, lineHeight: 1.3, letterSpacing: 'normal', whiteSpace: 'normal',
});
const framed = { display: 'flex', flexDirection: 'column', padding: '56px 80px 64px' };

function TitleSlide({ s, t, spec }) {
  return (
    <div style={{ ...base(t), background: `linear-gradient(125deg, ${t.hero}, ${t.hero2})`, color: t.heroText }}>
      <div style={{ position: 'absolute', left: 880, top: -180, width: 600, height: 600, borderRadius: '50%', background: t.accent2, opacity: 0.22 }} />
      <div style={{ position: 'absolute', left: 1040, top: 450, width: 380, height: 380, borderRadius: '50%', background: t.accent, opacity: 0.35 }} />
      <div style={{ position: 'absolute', left: 96, top: 170, width: 900 }}>
        <div style={{ fontSize: 60, height: 80, lineHeight: 1 }}>{s.icon || ''}</div>
        <div style={{ width: 88, height: 8, borderRadius: 4, background: t.accent2, margin: '12px 0 22px' }} />
        <div style={{ fontSize: (s.title || '').length <= 40 ? 60 : 46, fontWeight: 700, lineHeight: 1.12 }}>{s.title}</div>
        {s.subtitle && <div style={{ fontSize: 26, color: t.heroMuted, marginTop: 22 }}>{s.subtitle}</div>}
        {s.body && (
          <div style={{ marginTop: 26, maxHeight: 130, overflow: 'hidden' }}>
            <SlideMarkdown md={s.body} size={20} t={t} accent={t.accent2} color={t.heroMuted} muted={t.heroMuted} />
          </div>
        )}
      </div>
      {spec?.footer && <div style={{ position: 'absolute', left: 96, bottom: 24, fontSize: 14, color: t.heroMuted }}>{spec.footer}</div>}
    </div>
  );
}

function SectionSlide({ s, t, accent, sectionNo }) {
  return (
    <div style={{ ...base(t), background: accent, color: t.heroText }}>
      <div style={{ position: 'absolute', left: 900, top: 260, width: 620, height: 620, borderRadius: '50%', background: t.heroText, opacity: 0.08 }} />
      {s.icon && <div style={{ position: 'absolute', right: 80, top: 120, fontSize: 80 }}>{s.icon}</div>}
      <div style={{ position: 'absolute', left: 96, top: 180, width: 1040 }}>
        <div style={{ fontSize: 110, fontWeight: 700, color: t.heroMuted, lineHeight: 1 }}>{String(sectionNo).padStart(2, '0')}</div>
        <div style={{ fontSize: 54, fontWeight: 700, lineHeight: 1.15, marginTop: 30 }}>{s.title}</div>
        {s.subtitle && <div style={{ fontSize: 24, color: t.heroMuted, marginTop: 18 }}>{s.subtitle}</div>}
      </div>
    </div>
  );
}

function ContentSlide(p) {
  const { s, t, accent } = p;
  return (
    <div style={{ ...base(t), ...framed }}>
      <Header s={s} t={t} accent={accent} />
      <Body md={s.body} t={t} accent={accent} />
      <Footer {...p} />
    </div>
  );
}

function TwoColumnSlide(p) {
  const { s, t, accent } = p;
  const cols = (s.columns || ['', '']).slice(0, 2);
  return (
    <div style={{ ...base(t), ...framed }}>
      <Header s={s} t={t} accent={accent} />
      <FitText base={24} min={12} contentKey={cols.join('\u0000')} style={{ flex: 1 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', alignItems: 'start' }}>
          {cols.map((c, i) => (
            <div key={i} style={i ? { borderLeft: `2px solid ${t.border}`, paddingLeft: 40 } : { paddingRight: 40 }}>
              <SlideMarkdown md={c} t={t} accent={accent} />
            </div>
          ))}
        </div>
      </FitText>
      <Footer {...p} />
    </div>
  );
}

function ImageSideSlide(p) {
  const { s, t, accent, imageSrc, imageLeft } = p;
  const pic = (
    <div style={{ position: 'relative', width: 560, height: STAGE_H, flex: '0 0 560px' }}>
      <SlideImage src={imageSrc} alt={s.caption || s.title} t={t} />
      {s.caption && (
        <div style={{ position: 'absolute', left: 0, right: 0, bottom: 0, padding: '18px 20px', background: 'rgba(0,0,0,0.55)', color: '#fff', fontSize: 15 }}>{s.caption}</div>
      )}
    </div>
  );
  const text = (
    <div style={{ position: 'relative', flex: 1, display: 'flex', flexDirection: 'column', padding: '72px 60px 64px' }}>
      <Header s={s} t={t} accent={accent} />
      <Body md={s.body} t={t} accent={accent} base={24} />
      <Footer {...p} left={60} right={60} />
    </div>
  );
  return <div style={{ ...base(t), display: 'flex' }}>{imageLeft ? <>{pic}{text}</> : <>{text}{pic}</>}</div>;
}

function ImageFullSlide({ s, t, imageSrc }) {
  return (
    <div style={{ ...base(t), background: '#000' }}>
      <div style={{ position: 'absolute', inset: 0 }}><SlideImage src={imageSrc} alt={s.caption || s.title} t={t} /></div>
      <div style={{ position: 'absolute', left: 0, right: 0, top: 280, bottom: 0, background: 'linear-gradient(to bottom, rgba(0,0,0,0), rgba(0,0,0,0.78))' }} />
      <div style={{ position: 'absolute', left: 80, right: 80, bottom: 96, color: '#fff' }}>
        {s.icon && <div style={{ fontSize: 44, marginBottom: 8 }}>{s.icon}</div>}
        <div style={{ fontSize: 48, fontWeight: 700, lineHeight: 1.15 }}>{s.title}</div>
        {s.subtitle && <div style={{ fontSize: 24, color: '#e5e7eb', marginTop: 12 }}>{s.subtitle}</div>}
      </div>
      {s.caption && <div style={{ position: 'absolute', right: 80, bottom: 24, fontSize: 13, color: '#d1d5db' }}>{s.caption}</div>}
    </div>
  );
}

function QuoteSlide(p) {
  const { s, t, accent } = p;
  const text = plainText(s.body || '');
  return (
    <div style={{ ...base(t) }}>
      <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 16, background: accent }} />
      {s.title && <div style={{ position: 'absolute', left: 170, top: 84, fontSize: 16, fontWeight: 700, letterSpacing: 1, textTransform: 'uppercase', color: accent }}>{s.title}</div>}
      <div style={{ position: 'absolute', left: 64, top: 150, fontSize: 150, fontWeight: 700, color: accent, lineHeight: 1 }}>“</div>
      <FitText base={40} min={20} contentKey={text} style={{ position: 'absolute', left: 170, top: 160, width: 960, height: 340, display: 'flex', alignItems: 'center' }}>
        <div style={{ fontStyle: 'italic', lineHeight: 1.3 }}>{text}</div>
      </FitText>
      {s.subtitle && <div style={{ position: 'absolute', left: 170, top: 520, fontSize: 22, color: t.muted }}>— {s.subtitle}</div>}
      <Footer {...p} />
    </div>
  );
}

function Intro({ s, t, accent, height }) {
  if (!s.body) return null;
  return (
    <FitText base={20} min={14} contentKey={s.body} style={{ flex: `0 0 ${height}px` }}>
      <SlideMarkdown md={s.body} t={t} accent={accent} />
    </FitText>
  );
}

function StatsSlide(p) {
  const { s, t, accent } = p;
  const items = s.items || [];
  return (
    <div style={{ ...base(t), ...framed }}>
      <Header s={s} t={t} accent={accent} />
      <Intro s={s} t={t} accent={accent} height={70} />
      <div style={{ display: 'grid', gridTemplateColumns: `repeat(${items.length || 1}, 1fr)`, gap: 32, marginTop: 16, maxHeight: 330, flex: '0 1 330px' }}>
        {items.map((it, i) => {
          const v = it.value || '';
          return (
            <div key={i} style={{ background: t.surface, borderRadius: 18, borderLeft: `8px solid ${accent}`, padding: '28px 24px', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
              <div style={{ fontSize: v.length <= 5 ? 72 : (v.length <= 8 ? 56 : 40), fontWeight: 700, color: accent, lineHeight: 1.1 }}>{v}</div>
              <div style={{ fontSize: 22, fontWeight: 700, marginTop: 16 }}>{it.icon ? `${it.icon} ` : ''}{it.title}</div>
              {it.text && (
                <FitText base={17} min={11} contentKey={it.text} style={{ flex: 1, marginTop: 10 }}>
                  <SlideMarkdown md={it.text} t={t} accent={accent} color={t.muted} />
                </FitText>
              )}
            </div>
          );
        })}
      </div>
      <Footer {...p} />
    </div>
  );
}

function CardsSlide(p) {
  const { s, t, accent } = p;
  const items = s.items || [];
  const cols = items.length === 4 ? 2 : Math.max(1, Math.min(items.length, 3));
  return (
    <div style={{ ...base(t), ...framed }}>
      <Header s={s} t={t} accent={accent} />
      <Intro s={s} t={t} accent={accent} height={60} />
      <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gridAutoRows: '1fr', gap: 24, marginTop: 8 }}>
        {items.map((it, i) => (
          <div key={i} style={{ background: t.surface, borderRadius: 16, borderTop: `6px solid ${accent}`, padding: 24, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
            {it.icon && <div style={{ fontSize: 34, lineHeight: 1, marginBottom: 14 }}>{it.icon}</div>}
            {it.title && <div style={{ fontSize: cols >= 3 ? 20 : 22, fontWeight: 700, marginBottom: 8 }}>{it.title}</div>}
            {it.value && <div style={{ fontSize: 18, fontWeight: 700, color: accent, marginBottom: 6 }}>{it.value}</div>}
            {it.text && (
              <FitText base={17} min={11} contentKey={it.text} style={{ flex: 1 }}>
                <SlideMarkdown md={it.text} t={t} accent={accent} color={t.muted} />
              </FitText>
            )}
          </div>
        ))}
      </div>
      <Footer {...p} />
    </div>
  );
}

function TimelineSlide(p) {
  const { s, t, accent } = p;
  const items = s.items || [];
  const dense = items.length > 5;
  return (
    <div style={{ ...base(t), ...framed }}>
      <Header s={s} t={t} accent={accent} />
      <Intro s={s} t={t} accent={accent} height={60} />
      <div style={{ flex: 1, position: 'relative', display: 'flex', alignItems: 'center' }}>
        <div style={{ position: 'absolute', left: 0, right: 0, top: '50%', height: 4, marginTop: -2, background: accent, opacity: 0.35 }} />
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${items.length || 1}, 1fr)`, width: '100%', position: 'relative' }}>
          {items.map((it, i) => (
            <div key={i} style={{ display: 'grid', gridTemplateRows: '1fr 24px 1fr', height: 300, textAlign: 'center', padding: '0 8px' }}>
              <div style={{ alignSelf: 'end', fontSize: dense ? 15 : 18, fontWeight: 700, color: accent, paddingBottom: 14 }}>{it.value}</div>
              <div style={{ justifySelf: 'center', width: 24, height: 24, borderRadius: '50%', background: accent, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{ width: 10, height: 10, borderRadius: '50%', background: t.bg }} />
              </div>
              <div style={{ paddingTop: 14, overflow: 'hidden' }}>
                <div style={{ fontSize: dense ? 16 : 20, fontWeight: 700 }}>{it.icon ? `${it.icon} ` : ''}{it.title}</div>
                {it.text && <div style={{ fontSize: dense ? 13 : 16, color: t.muted, marginTop: 8, lineHeight: 1.3 }}>{plainText(it.text)}</div>}
              </div>
            </div>
          ))}
        </div>
      </div>
      <Footer {...p} />
    </div>
  );
}

const LAYOUTS = {
  title: TitleSlide,
  section: SectionSlide,
  content: ContentSlide,
  two_column: TwoColumnSlide,
  image_left: (p) => <ImageSideSlide {...p} imageLeft />,
  image_right: (p) => <ImageSideSlide {...p} imageLeft={false} />,
  image_full: ImageFullSlide,
  quote: QuoteSlide,
  stats: StatsSlide,
  cards: CardsSlide,
  timeline: TimelineSlide,
};

/**
 * One slide at stage size. `resolveImage(ref)` turns an `asset://name`, a bare
 * name or a URL into a src.
 */
export default function SlideStage({ slide, spec, number, total, sectionNo = 1, resolveImage }) {
  const t = deckTheme(spec);
  const s = slide || {};
  const accent = s.accent || t.accent;
  const Layout = LAYOUTS[s.layout] || ContentSlide;
  const imageSrc = s.image && resolveImage ? resolveImage(s.image) : (s.image || '');
  return (
    <Layout
      s={s}
      t={t}
      accent={accent}
      spec={spec}
      number={number}
      total={total}
      sectionNo={sectionNo}
      imageSrc={imageSrc}
    />
  );
}
