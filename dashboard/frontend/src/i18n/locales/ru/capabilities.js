// The tool capability model (tools/capabilities.py): capability names, the
// blocked combinations and the delegation suffix, as the agent editor and
// the MCP page show them. The backend text stays the fallback for a rule
// this file does not know.
export default {
  labels: {
    ingests_untrusted: 'принимает недоверенный текст',
    reads_private: 'читает приватные данные',
    can_exfiltrate: 'может отправлять данные наружу',
  },
  via: 'через',
  rules: {
    lethal_trifecta: {
      title: 'Смертельная тройка',
      explanation: 'Этот агент может принимать текст, подконтрольный злоумышленнику, читать приватные данные и отправлять данные наружу. Прочитанный текст может приказать ему собрать секреты и переслать их, и в цепочке нет ничего, что это остановит.',
    },
    exfiltration_path: {
      title: 'Канал утечки',
      explanation: 'Этот агент может принимать текст, подконтрольный злоумышленнику, и отправлять данные наружу. У внедрённых инструкций есть прямой канал наружу: всё, что уже есть в контексте агента, может через него уйти. Давайте такое сочетание только когда выход наружу и есть цель.',
    },
    system_workspace: {
      title: 'Правило системного воркспейса',
      explanation: 'Агент системного воркспейса никогда не может держать инструменты push, оболочки, делегирования и отправки наружу.',
    },
  },
  viaDelegation: {
    title: 'через делегирование',
    explanation: 'Это сочетание замыкается только через агентов, которым этот агент может делегировать. Сузьте его список делегатов, чтобы убрать путь.',
  },
};
