// Was das Tool-Gate aus einem Aufruf gemacht hat (tools/permission_policy.py).
export default {
  permission: {
    allow: 'erlaubt',
    deny: 'abgelehnt',
    ask: 'Rückfrage',
  },
  badgeTitle: 'Tool-Gate: {{permission}}, {{reason}}',
  detail: 'Tool-Gate',
  reasons: {
    default_allow: 'nichts gesetzt, läuft wie gewohnt',
    never_gated: 'nie geprüft',
    policy_always_allow: 'Tool-Richtlinie: immer erlauben',
    policy_always_ask: 'Tool-Richtlinie: immer fragen',
    approval_list: 'auf der Freigabeliste',
    auto_run: 'Klassifikator sagte ausführen',
    auto_deny: 'Klassifikator sagte ablehnen',
    auto_ask: 'Klassifikator sagte fragen',
    auto_unclear: 'Klassifikator konnte nicht entscheiden',
    hook_deny: 'ein Hook hat abgelehnt',
    hook_ask: 'ein Hook bat um eine Person',
    guardrail_deny: 'eine Reihenfolge-Regel hat ihn abgelehnt',
    guardrail_ask: 'eine Reihenfolge-Regel hat einen Menschen gefragt',
    human_approved: 'eine Person hat diesen Aufruf freigegeben',
    human_denied: 'eine Person hat ihn abgelehnt, oder niemand hat rechtzeitig geantwortet',
    think_required: 'abgelehnt, bis der Agent zuerst nachdenkt',
  },
};
