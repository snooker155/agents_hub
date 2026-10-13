// Eine Datenklasse sagt, wie sensibel die Arbeit eines Arbeitsbereichs ist (common/data_class.py).
export default {
  title: 'Datenklasse',
  label: 'Datenklasse dieses Arbeitsbereichs',
  explainer: 'Wie sensibel die Arbeit dieses Arbeitsbereichs ist. Sie entscheidet, welche verbundenen Knoten (andere Rechner, die ihre Modelle oder Sandbox verleihen) die Arbeit sehen dürfen. Ein Agent kann sie für sich verschärfen, nie lockern.',
  public: 'Öffentlich',
  internal: 'Intern',
  confidential: 'Vertraulich',
  desc: {
    public: 'Jeder Knoten darf die Arbeit sehen.',
    internal: 'Nur Knoten, denen der Hub-Administrator vertraut. Das ist die Voreinstellung.',
    confidential: 'Nur attestierte Knoten (Hardware, die ihr Besitzer nicht lesen kann). Es gibt noch keine, daher verlässt diese Arbeit diesen Hub nie.',
  },
  needs: 'Braucht einen Knoten mit mindestens: {{trust}}.',
  saved: 'Gespeichert',
  errors: { load: 'Die Datenklasse konnte nicht geladen werden.', save: 'Die Datenklasse konnte nicht gespeichert werden.' },
};
