import { useI18n } from '../i18n';

// What the tool gate made of one call (tools/permission_policy.py): allow,
// deny or ask, with the stable reason code as the tooltip. Drawn on a tool
// node of the process graph and in the call's detail. A call without the two
// fields (a run older than the trail, an imported agent) shows nothing.
const TONES = {
  allow: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  deny: 'bg-red-50 text-red-700 border-red-200',
  ask: 'bg-amber-50 text-amber-800 border-amber-300',
};

function policyReasonText(t, code) {
  if (!code) return '';
  const key = `policyTrail.reasons.${code}`;
  const text = t(key);
  return text === key ? code : text;
}

export default function PolicyBadge({ tool, withReason = false }) {
  const { t } = useI18n();
  const permission = tool?.evaluated_permission;
  if (!permission) return null;
  const permissionText = t(`policyTrail.permission.${permission}`);
  const reason = policyReasonText(t, tool.reason_code);
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-medium whitespace-nowrap ${TONES[permission] || TONES.allow}`}
      title={t('policyTrail.badgeTitle', { permission: permissionText, reason: reason || tool.reason_code || '' })}
      data-testid="policy-badge"
      data-reason-code={tool.reason_code || ''}
    >
      {permissionText}
      {withReason && reason ? <span className="font-normal">· {reason}</span> : null}
    </span>
  );
}
