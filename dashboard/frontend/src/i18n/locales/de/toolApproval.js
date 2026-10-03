// Ein Werkzeugaufruf, der in einem Chat-Zug auf eine Person wartet
// (common/tool_approvals.py, components/chat/ToolApprovalCard.jsx).
export default {
  title: 'Freigabe nötig',
  status: {
    pending: 'Wartet auf Sie',
    approved: 'Freigegeben',
    denied: 'Abgelehnt',
    expired: 'Niemand hat rechtzeitig geantwortet',
    cancelled: 'Der Lauf wurde gestoppt',
  },
  by: {
    hook: 'Ein Hook des Arbeitsbereichs',
    policy: 'Die Werkzeugrichtlinie',
    auto: 'Der Richtlinienklassifikator',
    guardrail: 'Eine Leitplanke',
  },
  noteLabel: 'Notiz für den Agenten',
  notePlaceholder: 'Eine Notiz für den Agenten (optional)',
  approve: 'Freigeben',
  deny: 'Ablehnen',
  waitsUntil: 'Wartet bis {{time}}, danach wird der Aufruf abgelehnt.',
  decidedBy: 'Beantwortet von {{name}}',
  noteShown: 'Notiz: {{note}}',
  waiting: 'Wartet auf Ihre Freigabe für {{tool}}',
  errors: {
    forbidden: 'Nur der Besitzer des Laufs oder ein Admin kann diesen Aufruf beantworten.',
    gone: 'Dieser Aufruf wartet nicht mehr auf eine Antwort.',
    failed: 'Die Antwort konnte nicht gesendet werden. Bitte erneut versuchen.',
  },
};
