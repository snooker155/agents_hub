// The tool capability model (tools/capabilities.py): capability names, the
// blocked combinations and the delegation suffix, as the agent editor and
// the MCP page show them. The backend text stays the fallback for a rule
// this file does not know.
export default {
  labels: {
    ingests_untrusted: 'nimmt nicht vertrauenswürdigen Text auf',
    reads_private: 'liest private Daten',
    can_exfiltrate: 'kann Daten nach außen senden',
  },
  via: 'über',
  rules: {
    lethal_trifecta: {
      title: 'Tödliche Trias',
      explanation: 'Dieser Agent kann von Angreifern steuerbaren Text aufnehmen, private Daten lesen und Daten nach außen senden. Gelesener Text kann ihn anweisen, Geheimnisse zu sammeln und weiterzuleiten, ohne dass etwas in der Kette das stoppt.',
    },
    exfiltration_path: {
      title: 'Abflusspfad',
      explanation: 'Dieser Agent kann von Angreifern steuerbaren Text aufnehmen und Daten nach außen senden. Eingeschleuste Anweisungen haben einen direkten Kanal nach außen: alles, was schon im Kontext des Agenten ist, kann darüber abfließen. Nur gewähren, wenn die Reichweite nach außen der Zweck ist.',
    },
    system_workspace: {
      title: 'Regel des System-Workspace',
      explanation: 'Ein Agent des System-Workspace darf nie ein Push-, Shell-, Delegations- oder Ausgangswerkzeug halten.',
    },
  },
  viaDelegation: {
    title: 'über Delegation',
    explanation: 'Diese Kombination schließt sich nur über Agenten, an die dieser delegieren darf. Schränken Sie seine Delegatenliste ein, um den Pfad zu entfernen.',
  },
};
