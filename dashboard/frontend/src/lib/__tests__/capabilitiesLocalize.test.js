import { describe, it, expect } from 'vitest';
import { translate } from '../../i18n/core';
import { checkCombination, localizeViolation, capabilityLabel } from '../capabilities';

const tRu = (k, vars) => translate('ru', k, vars);

describe('localizeViolation', () => {
  it('translates a client-side warning, its capability labels and the rule text', () => {
    const v = checkCombination(['web_search', 'notify_user'], {
      web_search: { capabilities: ['ingests_untrusted'] },
      notify_user: { capabilities: ['can_exfiltrate'] },
    });
    const out = localizeViolation(v, tRu);
    expect(out.title).toBe('Канал утечки');
    expect(out.explanation).toMatch(/недоверенн|злоумышленник/);
    expect(capabilityLabel('ingests_untrusted', tRu)).toBe('принимает недоверенный текст');
  });

  it('folds the delegation suffix back on and translates the via of each hop', () => {
    const server = {
      rule_id: 'lethal_trifecta_via_delegation',
      title: 'Lethal trifecta via delegation',
      explanation: 'english',
      capabilities: ['ingests_untrusted', 'reads_private', 'can_exfiltrate'],
      sources: { ingests_untrusted: ['via run_flow_tool -> researcher_agent -> via run_agent_tool -> web_searcher: fetch_url'] },
      blocking: true,
    };
    const out = localizeViolation(server, tRu);
    expect(out.title).toBe('Смертельная тройка через делегирование');
    expect(out.explanation).toMatch(/делегатов/);
    expect(out.sources.ingests_untrusted[0]).toBe('через run_flow_tool -> researcher_agent -> через run_agent_tool -> web_searcher: fetch_url');
  });

  it('keeps the backend text for a rule the locale does not know', () => {
    const out = localizeViolation({ rule_id: 'mystery', title: 'Mystery', explanation: 'x', sources: {} }, tRu);
    expect(out.title).toBe('Mystery');
  });
});
