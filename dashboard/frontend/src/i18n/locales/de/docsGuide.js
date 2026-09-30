// Guide sections of the Documentation page (pages/Docs.jsx, GuideDoc):
// docsGuide.<key>.nav / title / lead / s0..s5 { h, p?, points? } / callout?.
// Written from the corpus in /docs; each section links its corpus files as
// the full reference, so facts belong there first.
export default {
  "files": {
    "nav": "Dateien",
    "title": "Dateien des Arbeitsbereichs",
    "lead": "Eine Datei des Arbeitsbereichs wird einmal gespeichert und überall über ihre id verwendet: in einer Chat-Nachricht, in einem Speicher-Pool, in einer Aufgabe, in einem Evaluierungsfall oder in dem, was ein Agent selbst gespeichert hat. Laden Sie sie einmal auf der Seite [Dateien](/files) hoch, und all diese Stellen finden dieselbe Datei.",
    "s0": {
      "h": "Eine Datei finden",
      "p": "Die Seite [Dateien](/files) zeigt die Dateien des gewählten Arbeitsbereichs als Ordnerbaum oder als flache Tabelle, je nachdem, was Sie zuletzt gewählt haben; eine Suche öffnet jeden Ordner mit einem Treffer. Eine bestimmte Datei öffnen Sie direkt mit `/files?file=<id>`, dem Link, auf den ein Zitat im Chat oder ein Ergebnis von „wo verwendet\" führt."
    },
    "s1": {
      "h": "Hochladen und Grenzen",
      "points": [
        "Werden dieselben Bytes erneut hochgeladen, liefert das die schon vorhandene Datei zurück, sodass ein Dokument, das in zehn Chat-Nachrichten angehängt ist, eine Datei ist, nicht zehn.",
        "Eine einzelne Datei ist durch `AGENTS_HUB_FILES_MAX_FILE_MB` begrenzt (standardmäßig 25 MB), der gesamte Arbeitsbereich durch `AGENTS_HUB_FILES_MAX_WORKSPACE_MB` (1024 MB); ein Upload über einer der beiden Grenzen wird abgelehnt.",
        "Das Löschen einer Datei entfernt ihren Inhalt, behält aber einen Eintrag, sodass eine alte Chat-Nachricht oder ein Zitat weiter zeigt, was es war."
      ]
    },
    "s2": {
      "h": "Wo eine Datei verwendet wird",
      "points": [
        "Chat: Hängen Sie **Aus den Dateien des Arbeitsbereichs** in der Eingabe an, oder aktivieren Sie **Im Arbeitsbereich speichern** bei einem Upload, damit die Datei auch dort bleibt.",
        "Speicher: Der Dateien-Tab eines Pools kann eine Datei des Arbeitsbereichs hinzufügen; sie wird dann genauso für die Suche indexiert wie ein Upload.",
        "Aufgaben und Evaluierungen: Dateien, die einer Aufgabe oder einem Evaluierungsfall angehängt sind, werden bei jedem Lauf in ihr Arbeitsverzeichnis kopiert.",
        "Agenten: Ein Agent kann Dateien des Arbeitsbereichs mit eigenen Werkzeugen auflisten, lesen und speichern, und eine Chat-Antwort verlinkt, was er erzeugt hat."
      ]
    },
    "s3": {
      "h": "Zitate",
      "p": "Wenn ein Agent den Speicher durchsucht, kommen die verwendeten Abschnitte und Notizen nummeriert als `[n]` zurück, und die Antwort zeigt sie als Quellen unter dem Text. Ein Klick auf eine Quelle öffnet die dahinterliegende Datei des Arbeitsbereichs, oder, wenn es keine gibt, die Speicherseite."
    },
    "callout": "Dateien, die außerhalb des üblichen Wegs entstehen, etwa durch Claude Code, Codex oder eine Shell, werden nicht automatisch gelistet: nutzen Sie **Aus Ordner synchronisieren** auf der Seite Dateien, um sie zu registrieren."
  },
  "projectDeployments": {
    "nav": "Projekt-Deployment",
    "title": "Ein Projekt deployen",
    "lead": "Frontend und Backend eines Projekts können direkt im Hub laufen, mit einem Klick deployt oder durch den eigenen `deploy_project`-Werkzeugaufruf eines Agenten. Der Hub beobachtet die Dienste, startet einen abgestürzten neu und macht die App aus dem Dashboard, über einen externen Link und aus dem Browser des Agenten heraus erreichbar. Das alles lebt auf dem **Deploy**-Tab des Projekts; die Seite [Deployments](/deployments) listet alle deployten Apps des Arbeitsbereichs.",
    "s0": {
      "h": "Modi und Dienste",
      "p": "**Erkennen** schlägt Dienste anhand der Projektdateien vor: eine `package.json`, ein Python-Einstiegspunkt, eine `docker-compose.yml`, ein `Dockerfile` oder eine einfache `index.html`, jeder mit einer Art, einem Ordner, einem Port und seinen Installations- und Startbefehlen. **Konfigurieren** bearbeitet alles von Hand.",
      "points": [
        "**docker** (der Standard) startet einen Container pro Dienst im Netz des Hubs.",
        "**compose** startet die eigene `docker-compose.yml` des Projekts.",
        "**local** startet einfache Prozesse auf dem Host des Hubs ohne Isolation, für die eigene Maschine oder einen Host ohne Docker."
      ]
    },
    "s1": {
      "h": "Deployen, neu starten, stoppen",
      "points": [
        "**Deployen** stoppt, was läuft, baut, was gebaut werden muss, und startet jeden Dienst; der Status durchläuft Wird gebaut und Startet bis Läuft, oder zeigt Antwortet nicht oder Fehlgeschlagen, wenn ein Dienst nicht hochkommt.",
        "**Neu starten** macht dasselbe, ohne die Images neu zu bauen; **Neu bauen** entspricht **Deployen**; **Stoppen** fährt alles herunter, behält aber die Konfiguration und den Freigabelink.",
        "Ein Dienst, der von selbst endet, startet automatisch neu; drei Neustarts innerhalb von fünf Minuten pausieren das Deployment als Absturzschleife, die erst behoben werden muss, bevor erneut deployt wird.",
        "Das **Journal** auf dem Tab listet jedes Deployment, jeden Build, Start, Fehler, Neustart und jede Link-Änderung."
      ]
    },
    "s2": {
      "h": "Die App sehen",
      "points": [
        "Im Dashboard zeigt der Deploy-Tab den Hauptdienst live; **Anzeigen** wechselt den Rahmen auf einen anderen Dienst.",
        "Von außen ist die App unter `/apps/<slug>/` erreichbar; solange der Link privat ist, braucht er den Freigabeschlüssel des Deployments, **Öffentlich machen** entfernt den Schlüssel und **Link zurücksetzen** ersetzt Schlüssel und Slug.",
        "**Im Agenten-Browser öffnen** öffnet dieselbe Adresse auf der Seite [Browser](/browser), damit der Agent testen kann, was er gerade gebaut hat."
      ]
    },
    "s3": {
      "h": "Logs",
      "p": "**Logs** auf dem Tab folgen live der Ausgabe eines Dienstes; der Tab **Build** zeigt stattdessen die Ausgabe des Image-Builds."
    },
    "callout": "Ein Dev-Server, der nur an localhost bindet, ist im Docker- oder Compose-Modus nicht erreichbar: geben Sie `--host 0.0.0.0` (oder das passende Flag) mit, damit er auf allen Schnittstellen lauscht."
  },
  "registry": {
    "nav": "Agentenregister",
    "title": "Agentenregister",
    "lead": "Eine Seite für den ganzen Hub beantwortet zwei Fragen: welche Agenten, Flows und Skills es gibt, wem jeder gehört und ob er freigegeben werden darf, und welche MCP-Server angehängt sind und ob ein Administrator tatsächlich für sie bürgt. Beide Hälften sind standardmäßig ausgeschaltet, sodass sich ohne Aktivierung nichts ändert, außer dass es jetzt eine Stelle zum Nachsehen gibt.",
    "s0": {
      "h": "Besitzer und Prüfung",
      "points": [
        "Jeder Agent, Flow und Skill hat einen Besitzer, den Benutzer, der ihn angelegt hat, und einen Prüfstatus: Entwurf, in Prüfung, genehmigt oder abgelehnt.",
        "Ist der Prüf-Schalter des Hubs aus, verhält sich das Teilen wie bisher, und der Status wird nur festgehalten, aber nichts wird erzwungen.",
        "Ist der Schalter an, bleibt ein geteiltes Element auf in Prüfung, statt gelistet zu werden, und im [Marktplatz](/marketplace) erscheinen nur genehmigte Einträge.",
        "Wird der eigentliche Inhalt eines schon genehmigten Elements geändert, die Anweisungen eines Agenten, die Knoten eines Flows, die Schritte eines Skills, geht es automatisch zurück auf in Prüfung.",
        "Ein Besitzer (oder ein Administrator) reicht ein Element zur Prüfung ein; der Besitzer eines abgelehnten Elements kann es bearbeiten und erneut einreichen."
      ]
    },
    "s1": {
      "h": "MCP-Zulassungsliste",
      "points": [
        "Getrennt von der Prüfung: ein Katalog von MCP-Servern, den ein Administrator pflegt, mit eigenem Schalter, der die angehängten Server eines Arbeitsbereichs daran bindet.",
        "Einen Katalogeintrag kann jeder anfragen; ein Administrator genehmigt oder sperrt ihn."
      ]
    },
    "s2": {
      "h": "Die Seite",
      "points": [
        "Die Tabs **Agenten**, **Flows** und **Skills** listen jedes Element dieser Art über alle Arbeitsbereiche, filterbar nach Status, Besitzer und Arbeitsbereich, mit **Zur Prüfung einreichen** für den Besitzer und **Genehmigen** oder **Ablehnen** für einen Administrator.",
        "**MCP Server** listet jeden irgendwo angehängten Server, mit einem Hinweis, ob er einem genehmigten Katalogeintrag entspricht, einem Antragsformular für alle und **Genehmigen** oder **Sperren** für einen Administrator.",
        "Die beiden Hub-Schalter stehen oben auf der Seite, sichtbar und bearbeitbar nur für einen Administrator."
      ]
    },
    "callout": "Für Flows und Skills gibt es noch keine CLI-Befehle zur Prüfung: nutzen Sie die Seite [Agentenregister](/agent-registry) oder die API. Nur Agenten lassen sich mit `ah agent review list|submit|approve|reject` prüfen."
  },
  "browser": {
    "nav": "Browser",
    "title": "Der Browser",
    "lead": "Eine Live-Ansicht dessen, was die Browser-Sitzung eines Agenten gerade tut, mit der Möglichkeit, die Steuerung zu übernehmen, sowie eine Seite für freies Surfen, deren Sitzung an einen Agenten übergeben werden kann. Dafür muss der Browser-Dienst im Abschnitt **Browser** der Seite [Einstellungen](/settings) eingerichtet sein; ohne ihn sagt die Seite das selbst.",
    "s0": {
      "h": "Einrichtung",
      "points": [
        "Der Abschnitt **Browser** unter [Einstellungen](/settings) legt Adresse und Token des Dienstes fest, startet ihn und zeigt sein Protokoll, alles ohne Neustart.",
        "Ein lokaler Prozess führt den Dienst auf dem Host des Hubs aus; ein Docker-Container führt ihn isoliert aus und wird nur angeboten, solange Docker antwortet.",
        "Der lokale Modus braucht eine Chromium-Installation für Playwright, die derselbe Abschnitt mit einem Klick anbietet."
      ]
    },
    "s1": {
      "h": "Den Browser eines Laufs beobachten",
      "points": [
        "Ein Lauf, der ein Browser-Werkzeug aufruft, bekommt ein **Browser**-Panel in seiner Live-Ausgabe, das die Seite so zeigt, wie sie gezeichnet wird.",
        "Kann der Live-Stream nicht verbinden, fragt das Panel stattdessen etwa einmal pro Sekunde ein Bild ab."
      ]
    },
    "s2": {
      "h": "Steuerung übernehmen",
      "points": [
        "**Steuerung übernehmen** macht aus dem Bild Klicks, Eingaben und Scrollen auf genau der Sitzung, die der Agent nutzt, gedacht für einen Login, ein Captcha oder einen Cookie-Hinweis, den ein Modell besser nicht selbst erledigt.",
        "Es pausiert den Agenten beim nächsten Browserschritt, bis Sie **Freigeben** drücken, die Seite verlassen, oder ein Leerlauf-Timeout die Steuerung von selbst freigibt.",
        "Wohin Sie navigieren, wird gegen die Domain-Richtlinie des Arbeitsbereichs geprüft, genau wie beim Agenten selbst."
      ]
    },
    "s3": {
      "h": "Freies Surfen und Übergabe",
      "points": [
        "**Browser** in der Navigation ist freies Surfen auf demselben Dienst: Arbeitsbereich wählen, eine **Neue Sitzung** öffnen und selbst steuern.",
        "**An Agenten übergeben** auf einer offenen Sitzung legt eine Aufgabe für den gewählten Agenten mit der angehängten Sitzung an, sodass sein Lauf auf derselben Seite mit denselben Cookies weitermacht.",
        "**Im Agenten-Browser öffnen** auf dem **Deploy**-Tab eines Projekts (siehe [Projekte](/projects)) öffnet hier direkt die deployte App, damit der Agent sie testen kann."
      ]
    },
    "callout": "Eine Sitzung schließt sich nach einem Leerlauf-Timeout von selbst; Zusehen zählt als Nutzung, daher bleibt sie offen, solange jemand zuschaut."
  },
  "agentLoop": {
    "nav": "Agenten-Loop",
    "title": "Agenten-Loop, Tools und Guardrails",
    "lead": "Der Agenten-Loop läuft zwischen den Modellaufrufen eines Runs ab: Tool-Ergebnisse werden zu Nachrichten, das Modell wird erneut aufgerufen, und das wiederholt sich, bis es antwortet. Die Tool-Policy entscheidet über jeden Tool-Aufruf, und Guardrails prüfen, was hinein und was heraus geht.",
    "s0": {
      "h": "Was einen Run begrenzt",
      "p": "Innerhalb des Loops kann eine Reihe von Richtlinien wirken, jede einzeln pro Agent aktiviert. Ein Agent, der keine davon nutzt, läuft genau wie zuvor.",
      "points": [
        "Das Eingreifen in einen laufenden Run erlaubt es, dem Agenten eine Nachricht zu senden oder den Run zu unterbrechen, während er arbeitet.",
        "Die Kontextverdichtung kürzt die Historie, sobald ein langer Run sich dem Kontextfenster des Modells nähert.",
        "Die Tool-Suche verbirgt Tools hinter `search_tools`, sobald ein Agent mehr Tools hat als der Schwellenwert des Workspace.",
        "Die strukturierte Ausgabe prüft die endgültige Antwort gegen ein JSON Schema und unternimmt Reparaturversuche bei einer Abweichung.",
        "Fallback-Modelle wiederholen einen fehlgeschlagenen Aufruf beim nächsten Modell in der Liste, mit denselben Tools."
      ]
    },
    "s1": {
      "h": "Welche Tools laufen dürfen",
      "p": "Drei Modi legen fest, was ein Tool-Aufruf darf: pro Tool auf dem **Tools**-Tab des Agenten festgelegt, oder pro Workspace unter [Einstellungen](/settings).",
      "points": [
        "`always_allow` führt den Aufruf ohne Nachfrage aus.",
        "`always_ask` hält den Aufruf zur Freigabe zurück; im Chat bekommt der Agent stattdessen eine Ablehnung mit dem Hinweis, um Erlaubnis zu bitten.",
        "`auto` schickt den Aufruf an ein kleines Modell, das mit run, deny oder ask antwortet.",
        "Der erste Treffer gewinnt: der eigene Eintrag des Agenten für das Tool, dann sein `*`, dann der Eintrag des Workspace, dann dessen `*`."
      ]
    },
    "s2": {
      "h": "Guardrails",
      "p": "Ein Guardrail prüft die Startanweisung eines Runs, seine endgültige Antwort oder beides, per Regel oder per Judge-Modell. Verwaltet auf der Seite [Guardrails](/guardrails).",
      "points": [
        "Regeltypen sind `regex`, `keywords`, `pii` und `max_chars`; sie werden zuerst geprüft und kosten nichts.",
        "Ein `judge`-Guardrail fragt ein Modell, ob der Text eine von Ihnen formulierte Anweisung verletzt.",
        "`block` beendet den Run mit dem Status `guardrail_tripped`; `warn` protokolliert den Befund und lässt den Run weiterlaufen.",
        "Ein Guardrail gilt für alle Agenten oder nur für die, die ihn auf ihrem **Config**-Tab auswählen."
      ]
    },
    "s3": {
      "h": "Was der Run festhält",
      "points": [
        "Ein Run, dessen Loop mehr als nur Tools aufgerufen hat, trägt einen `loop`-Block: welches Modell jeden Aufruf beantwortet hat, Verdichtungen, Steuerungsnachrichten, geladene Tools, Guardrail-Prüfungen und Entscheidungen der Tool-Policy.",
        "Die Run-Seite zeigt all das im Loop-Panel, neben der Agentenversion, die gelaufen ist."
      ]
    },
    "callout": "Ein blockiertes Guardrail oder ein abgelehnter Tool-Aufruf erscheint trotzdem als gewöhnlicher Run, der nur früher endete: prüfen Sie zuerst den Fehler, bevor Sie annehmen, dass sich der Agent falsch verhalten hat."
  },
  "steering": {
    "nav": "Steuerung & Übergabe",
    "title": "Eingreifen in einen Agenten, Gesprächsübergabe und der Seiten-Chat",
    "lead": "Ein laufender Agent ist keine Blackbox: Sie können ihm während der Arbeit eine Nachricht schicken, ein Gespräch an einen anderen Agenten übergeben oder einen Chat öffnen, der an die Seite gebunden ist, auf der Sie sich gerade befinden.",
    "s0": {
      "h": "In einen laufenden Run eingreifen",
      "p": "Verfügbar, solange der Status eines Runs `running` ist: über das Chat-Eingabefeld oder das Steer/Interrupt-Feld auf einer Task- oder Run-Seite.",
      "points": [
        "**Steer** platziert Ihre Nachricht vor dem nächsten Modellschritt des Agenten, ohne ihn zu stoppen.",
        "**Interrupt** stoppt den Run wie **Stop** und startet ihn dann mit Ihrer Nachricht am Ende neu.",
        "**Queue** wartet im Chat, bis die aktuelle Runde beendet ist, bevor Ihre Nachricht eingeht.",
        "Eine Nachricht, die eintrifft, während der Agent seine endgültige Antwort schreibt, geht nicht verloren: dafür läuft noch ein zusätzlicher Durchgang."
      ]
    },
    "s1": {
      "h": "Ein Gespräch an einen anderen Agenten übergeben",
      "p": "Eine Übergabe unterscheidet sich von einer Delegation: der empfangende Agent antwortet dem Nutzer direkt, und das Gespräch bleibt danach bei ihm.",
      "points": [
        "Eingestellt auf dem **Tools**-Tab des Agenten, Karte **Handoffs**: die Liste **May hand over to** legt fest, an wen er übergeben darf, und ein zweiter Punkt, wie viel Verlauf die Gegenseite sieht.",
        "Ohne ausgewähltes Ziel besitzt der Agent gar kein Übergabe-Tool.",
        "Die Verlaufsfilter reichen vom breitesten zum engsten: `full`, `summary`, `last_n:<N>`, `none`.",
        "Eine Runde übergibt höchstens ein paar Mal (standardmäßig dreimal), und das Gespräch geht nie an einen Agenten zurück, der es schon einmal hatte."
      ]
    },
    "s2": {
      "h": "Der Seiten-Chat",
      "p": "Die Schaltfläche unten rechts auf jeder Seite, die ein Gespräch über das führt, was die Seite gerade zeigt.",
      "points": [
        "Manche Seiten, etwa ein Szenario, eine Loop, ein Team, die Definition eines Agenten oder ein Memory-Pool, haben ihren eigenen Chat mit eigenem Agenten; das Panel zeigt nur dieses Gespräch.",
        "Alle anderen Seiten teilen sich einen festen Assistenten, der den Titel und die URL der Seite sowie die dort gezeigten Datensätze sieht.",
        "Zur gleichen Seite zurückzukehren öffnet dasselbe Gespräch wieder; **Clear** beginnt ein neues.",
        "Er legt nie etwas an, ändert oder löscht nie etwas ohne ein klares Ja zu genau dieser Aktion."
      ]
    },
    "callout": "Jede Runde des Seiten-Chats ist ein gewöhnlicher Run: er erscheint unter [Läufe](/messages) mit eigenem Log und eigenen Kosten."
  },
  "mcp": {
    "nav": "MCP-Server",
    "title": "MCP-Server",
    "lead": "Ein MCP-Server ist eine fremde Sammlung von Tools. Binden Sie ihn auf der Seite [MCP-Server](/mcp) an, und seine Tools werden zu gewöhnlichen Tools, die Sie einem Agenten zuweisen können.",
    "s0": {
      "h": "Einen Server anbinden",
      "p": "Pro Workspace konfiguriert: ein Server, der in einem Workspace angebunden ist, bleibt für Agenten unsichtbar, die in einem anderen gebaut werden.",
      "points": [
        "Wählen Sie einen Transport: `stdio` (ein Befehl mit Argumenten, als Kindprozess gestartet), `streamable_http` oder `sse` (eine URL mit Headern) oder `websocket` (nur eine URL).",
        "Die Schaltfläche **Test** verbindet sich mit dem Server, listet seine Tools auf und zeigt, welche davon die aktuelle Allowlist behält."
      ]
    },
    "s1": {
      "h": "Tools an einen Agenten vergeben",
      "p": "Die Server-ID wird zur Hälfte jedes Tool-Namens, den er erzeugt.",
      "points": [
        "`mcp:<server_id>` vergibt den ganzen Server.",
        "`mcp__<server_id>__<tool_name>` vergibt ein einzelnes Tool.",
        "Die ID lässt sich nachträglich nicht ändern; löschen Sie den Server und legen Sie ihn neu an, statt ihn umzubenennen."
      ]
    },
    "s2": {
      "h": "Capabilities und Freigabe",
      "p": "Ein entferntes Tool kann sich nicht selbst einstufen, deshalb legen Sie seine Capabilities einmal pro Server fest.",
      "points": [
        "Kreuzen Sie an, ob der Server nicht vertrauenswürdige Inhalte verarbeitet, private Daten liest oder Daten nach außen senden kann.",
        "Ein Agent, dessen Tools alle drei Punkte gleichzeitig erfüllen würden, wird beim Speichern abgelehnt.",
        "Die Freigabe kann für keines der Tools des Servers gelten, für alle oder für eine ausgewählte Liste, genau wie bei den eingebauten Tools.",
        "Ein `stdio`-Server startet in einer bereinigten Umgebung: er sieht nie die eigenen Provider-Schlüssel des Backends."
      ]
    },
    "s3": {
      "h": "Wenn ein Agent weniger Tools hat als erwartet",
      "points": [
        "Ein Server, der sich nicht verbinden lässt, wird übersprungen, nicht als Fehler behandelt: der Agent wird mit den Tools gebaut, die sich auflösen ließen.",
        "Eine Tool-Anzahl, die noch nicht geladen wurde, ist nicht dasselbe wie null; öffnen Sie den Server oder klicken Sie auf **Test**, damit er sich verbindet.",
        "Das Löschen eines Servers ändert nicht die Agenten, die ihn genannt haben; sie verlieren einfach diese Tools."
      ]
    },
    "callout": "Eine größere Installation kann eine hub-weite Allowlist aktivieren (der MCP-Katalog auf [Agentenregister](/agent-registry)), sodass ein Workspace nur einen von einem Admin genehmigten Server anbinden darf."
  },
  "outcomes": {
    "nav": "Ergebnisbewertung & Experimente",
    "title": "Ergebnisbewertung (Outcomes) und Experimente",
    "lead": "Ein Outcome bewertet das Ergebnis einer Task anhand einer Rubrik und kann sie für einen weiteren Versuch zurückschicken. Ein Experiment vergleicht zwei Versionen eines Agenten im echten Datenverkehr statt an einer Handvoll Testprompts.",
    "s0": {
      "h": "Outcome-Rubriken",
      "p": "Eine Markdown-Rubrik ist eine Liste von Kriterien: jeder Punkt der obersten Ebene ist eines, und eine Rubrik ohne Punkte fällt auf ein einziges Kriterium namens Overall zurück.",
      "points": [
        "Nach jedem abgeschlossenen Run der Task bewertet ein unabhängiges Grader-Modell jedes Kriterium und entscheidet über Bestehen oder erneuten Versuch.",
        "`max_iterations` (1 bis 10, Standard 3) begrenzt, wie viele Versuche eine Rubrik erhält.",
        "Das Outcome besteht, wenn jedes Kriterium besteht oder der Mittelwert einen optionalen `threshold` erreicht.",
        "Bei einem erneuten Versuch erhält der Agent sein vorheriges Feedback unter **Outcome review of your previous attempt**."
      ]
    },
    "s1": {
      "h": "Blockieren und Bewertung auf Abruf",
      "points": [
        "Sind die Versuche aufgebraucht und Kriterien nicht erfüllt, wird die Task blockiert und eine Dashboard-Benachrichtigung verschickt.",
        "**Grade now** bewertet den letzten abgeschlossenen Run, ohne etwas neu zu starten, und verbraucht keinen Versuch.",
        "Erhöhen Sie die Versuchsanzahl oder ändern Sie die Rubrik und starten Sie die Task neu, um es erneut zu versuchen."
      ]
    },
    "s2": {
      "h": "A/B-Experimente an einem Agenten",
      "p": "Jede Änderung an einem Agenten wird in seiner **Version History** auf dem Config-Tab als Schnappschuss gespeichert. Ein Experiment benennt zwei oder mehr dieser gespeicherten Versionen, Arme genannt, mit einem Anteil der Runs für jeden.",
      "points": [
        "Eingestellt auf dem Config-Tab des Agenten, Karte **Experiment**: Version A wählen, ihren Anteil, Version B, und starten.",
        "Das Routing ist deterministisch: eine Chat-Konversation landet immer im selben Arm, ebenso eine gegebene Run-ID.",
        "Der Bericht listet pro Arm die Runs, die Anzahl der abgeschlossenen und fehlgeschlagenen, die mittleren Kosten, die mittlere Tokenzahl, die mittlere Dauer, den mittleren Score und die Erfolgsquote.",
        "Ein Agent hat höchstens ein offenes Experiment gleichzeitig."
      ]
    },
    "s3": {
      "h": "Ein Experiment beenden",
      "points": [
        "Das Beenden schickt jeden Run zur aktuellen Definition des Agenten zurück; nichts am gewinnenden Arm wird automatisch übernommen.",
        "Setzen Sie über **Version History** auf die gewinnende Version zurück, wenn Sie sie behalten möchten."
      ]
    },
    "callout": "Mittlerer Score und Erfolgsquote bleiben leer, solange der Agent keine Online-Eval-Regel hat, die seine Runs bewertet."
  },
  "sessionsRuns": {
    "nav": "Sessions & Runs",
    "title": "Sessions und Runs",
    "lead": "Ein Run ist ein einzelner Agentenaufruf, die kleinste Einheit, die das Dashboard zeigt. Sessions und Run-Gruppen zeigen, wie Runs zusammengehören.",
    "s0": {
      "h": "Runs",
      "p": "Jede Chat-Nachricht, jeder Flow-Knoten, jeder Szenario-Takt und jede Delegation ist ein Run.",
      "points": [
        "Der Status ist `running`, `completed`, `failed` oder `stopped`.",
        "Ein Run trägt seinen Agenten, sein Modell, seinen Provider, Eingabe, Ausgabe, Tokenzahlen, Dauer und, wenn er fehlgeschlagen ist, einen Fehler.",
        "Hat der Agenten-Loop mehr getan als nur Tools aufzurufen, zeigen der `loop`-Block des Runs und das Loop-Panel der Run-Seite, was passiert ist."
      ]
    },
    "s1": {
      "h": "Sessions und Run-Gruppen",
      "p": "Eine Session gruppiert die Runs einer Konversation, einer Task oder eines Flows. Eine Run-Gruppe zeigt, was eine Reihe von Runs überhaupt ausgelöst hat.",
      "points": [
        "Fünf Arten besitzen jeweils eine Reihe von Runs: Flow, Loop, Team, Szenario und Container.",
        "Die Seite [Laufgruppen](/run-groups) listet sie artübergreifend, jeweils mit Status, Gesamtkosten und ihren Kindern.",
        "Das Stoppen einer Gruppe stoppt alles, was sie besitzt, rekursiv bis hinunter zu den einzelnen Agent-Runs."
      ]
    },
    "s2": {
      "h": "Einen Fehler lesen",
      "points": [
        "Filtern Sie [Läufe](/messages) oder [Sitzungen](/sessions) nach Status `failed` und einem Zeitfenster.",
        "Das `error`-Feld des Runs reicht meist allein aus; öffnen Sie das Log, wenn nicht.",
        "Ein Run, den ein Guardrail gestoppt hat, endet mit dem Status `guardrail_tripped` und einem Fehler, der das Guardrail nennt.",
        "Ein Lauf einer Aufgabe, der sein Geldlimit erreicht, wird pausiert statt als fehlgeschlagen beendet, und die Aufgabe wartet auf Freigabe."
      ]
    },
    "s3": {
      "h": "Veraltete Runs und Live-Updates",
      "points": [
        "Ein Run, der noch lange als `running` markiert ist, nachdem alles längst fertig sein müsste, ist ein veralteter Run und keine laufende Arbeit: der Watchdog setzt ihn anhand seines letzten Heartbeats fort oder lässt ihn fehlschlagen.",
        "Das Dashboard verfolgt Sessions und Runs über einen einzigen gemeinsamen Stream und spielt nach einer kurzen Unterbrechung verpasste Ereignisse nach."
      ]
    },
    "callout": "Ein Agent, der wiederholt mit unterschiedlichen Fehlermeldungen scheitert, deutet meist auf ein Problem im Agenten hin; dieselbe Meldung bei mehreren Agenten deutet eher auf das Modell oder die Infrastruktur hin."
  },
  "services": {
    "nav": "Services",
    "title": "Services",
    "lead": "Ein Service hält einen Agenten dauerhaft als Menge von Kopien am Laufen, statt als eine von Hand gestartete Instanz: wie viele Replicas, wo sie laufen und wie viel sie ausgeben dürfen. Die Seite **Dienste** listet auch Runner: Services ohne Agent, die den Chat für jeden Agenten beantworten.",
    "s0": {
      "h": "Deploy und Run",
      "p": "**Run** auf der Seite eines Agenten startet eine Instanz, mit der Sie sprechen. **Deploy** auf der Seite [Dienste](/services) erstellt einen Service: den Agenten, den Workspace und das [Environment](/environments), eine minimale und maximale Anzahl von Replicas, wie viele Gespräche jede Replica gleichzeitig beantwortet, ob Replicas Aufgaben übernehmen, wann eine untätige Replica gestoppt wird, ein Geldlimit pro Turn und eine feste Version."
    },
    "s1": {
      "h": "Wo ein Chat-Turn läuft",
      "points": [
        "Jeder Chat-Turn, ob von der Chat-Seite, einem eingebetteten Widget, Telegram oder `/v1/chat/completions` mit einem Agenten-Modell, wird an eine Service-Replica übergeben statt im Backend selbst zu laufen.",
        "Ein Agent mit eigenem Service nutzt diesen; ein Agent ohne eigenen Service nutzt den Runner des Workspace, der bei erster Nutzung automatisch angelegt wird.",
        "Dasselbe Gespräch kehrt immer zur bereits antwortenden Replica zurück, damit der Verlauf in der richtigen Reihenfolge bleibt."
      ]
    },
    "s2": {
      "h": "Replicas und der Supervisor",
      "points": [
        "Eine Replica ist eine resident instance mit eigener Seite, eigenen Gesprächen, Prozess und Logs; die Liste **Instanzen** lässt sich nach Service filtern.",
        "Ein Supervisor bringt jeden Service auf sein Minimum, stoppt Replicas, die länger als `idle_stop_seconds` untätig waren, und pausiert einen Service, dessen Replicas ständig abstürzen, statt ihn endlos neu zu starten.",
        "Alles, was der Supervisor tut, wird im Events-Tab des Service protokolliert."
      ]
    },
    "s3": {
      "h": "Budget und Veröffentlichung",
      "points": [
        "**budget_usd** begrenzt die Ausgaben pro Turn, einschließlich allem, was er delegiert; erreicht ein Turn das Limit, endet er als fehlgeschlagener Turn.",
        "**Publish** gibt einem Service eine öffentliche Adresse, genau wie bei einer veröffentlichten Instance: ein Token und ein Verbindungsverlauf.",
        "Eine Nachricht kann einem Service auch direkt über die API geschickt werden, ohne über den Chat zu gehen."
      ]
    },
    "callout": "Das Pausieren eines Service stoppt jede Replica und weist neue Turns für dessen Agent zurück, statt sie an den Runner zu senden."
  },
  "deployments": {
    "nav": "Deployments",
    "title": "Deployments, Environments und Sandboxes",
    "lead": "Die Seite **Deployments** ist eine Arbeitsansicht über die geplanten Jobs, die tatsächlich produktive Arbeit leisten: agent-, flow- und loop-Jobs von der Seite [Plan](/plan), jeder mit eigenem Geldlimit, eigenem Environment und einem Protokoll jeder Auslösung. Ein **Environment** bestimmt, wo ein Lauf tatsächlich ausgeführt wird und was er erreichen kann.",
    "s0": {
      "h": "Was ein Deployment hinzufügt",
      "points": [
        "**environment_id**: die Environment, in der jede vom Job erzeugte Aufgabe läuft.",
        "**budget_usd**: das Geldlimit, das auf jede vom Job erzeugte Aufgabe kopiert wird.",
        "**auto_pause_after**: nach wie vielen aufeinanderfolgenden fehlgeschlagenen Auslösungen der Job automatisch pausiert wird, `0` schaltet dies ab, Standard `3`.",
        "**agent_version** (nur bei Agent-Jobs): den Job auf eine gespeicherte Agentenversion festlegen."
      ]
    },
    "s1": {
      "h": "Das Auslösungsprotokoll",
      "p": "Jeder Auslöseversuch wird erfasst, erfolgreich oder nicht: wann er stattfand, ob er automatisch war oder ein manuelles **Run now**, und bei Fehlschlag ein Fehlertyp wie `budget_exceeded`, `agent_missing` oder `capacity`. Die Detailansicht eines Deployments zeigt dieses Protokoll mit einem Nur-Fehler-Filter.",
      "points": [
        "Ein Job, der ständig fehlschlägt, pausiert sich selbst; existiert das Ziel (Agent, Flow oder Loop) nicht mehr, pausiert er sofort.",
        "**Run now** löst einen Job sofort aus, auch pausiert, damit eine Korrektur getestet werden kann, ohne ihn vorher fortzusetzen."
      ]
    },
    "s2": {
      "h": "Environments",
      "points": [
        "Die Seite [Umgebungen](/environments) enthält benannte Ausführungsprofile: lokaler Prozess oder Docker, ein Basis-Image und Pakete, Ressourcenlimits und einfache Umgebungsvariablen.",
        "Das **Netzwerk** kennt drei Einstellungen: unrestricted (normaler ausgehender Zugriff), limited (nur eine Liste erlaubter Hosts plus Paket-Registries) und none (dieselbe Absicherung mit leerer Liste).",
        "Ein limited- oder none-Netzwerk wird auf Container-Ebene nur vollständig durchgesetzt, wenn der Egress-Proxy aktiviert ist; sonst verlassen Sie sich nur auf die eigenen Tools des Hubs, die nicht erlaubte Hosts ablehnen.",
        "Das Archivieren einer Environment friert sie ein: sie kann nicht mehr ausgewählt oder bearbeitet werden, aber alles, was sie bereits nutzt, läuft weiter."
      ]
    },
    "s3": {
      "h": "Sandbox-Provider",
      "p": "Eine Sandbox führt einen einzelnen Codeschnipsel aus dem Tool `run_code` oder dem Code-Panel im Chat aus, getrennt davon, wo der Agent selbst läuft. **sandbox_provider** einer Environment wählt welchen: `docker` (Standard, mit durchgesetzter Netzwerkabsicherung), `local` (einfacher Subprozess, keine Isolation), `e2b` oder `modal` (entfernte, kurzlebige Umgebungen).",
      "points": [
        "`run_code` selbst hat standardmäßig überhaupt kein Netzwerk, solange die Environment nicht mehr erlaubt.",
        "Die Provider-Auswahl auf der Seite Environments und die `sandbox`-Prüfung des Doctors zeigen, welche Provider tatsächlich verfügbar sind."
      ]
    },
    "callout": "Eine Environment mit `network: none` ist nicht vollständig offline: die Absicherung gilt nur für die eigenen Tools des Hubs und, bei aktivem Egress-Proxy, für Clients, die sich daran halten. Ein Prozess, der direkt einen eigenen Socket öffnet, wird nicht gestoppt, solange die Absicherung auf Container-Ebene über den Proxy nicht aktiv ist."
  },
  "localModels": {
    "nav": "Lokale Modelle",
    "title": "Lokale Modelle und der Hub als Provider",
    "lead": "Die Seite [Modelle](/models) verwaltet lokale Modell-Server: entweder ein bereits laufendes Ollama oder die eigene Laufzeit des Hubs, die GGUF-Dateien direkt bereitstellt. Ein geladenes lokales Modell steht jedem Agenten wie jeder andere Provider zur Verfügung, und der Hub selbst kann als OpenAI-kompatibler Provider unter `/v1` aufgerufen werden.",
    "s0": {
      "h": "Ollama",
      "points": [
        "Der Hub liest Ollama über eine konfigurierbare Basis-URL aus und listet jedes Modell mit Größe, Familie und Quantisierung.",
        "**Pull** lädt ein Modell als Hintergrundjob herunter; **Delete** entfernt es.",
        "Ein geladenes Modell erscheint erst nach **Discover** für den Provider `ollama` in der Modellauswahl."
      ]
    },
    "s1": {
      "h": "Die Laufzeit des Hubs",
      "p": "Die eigene Laufzeit des Hubs startet für jede geladene GGUF-Datei einen `llama-server`-Prozess und stellt sie alle unter einer OpenAI-kompatiblen Adresse bereit. Geladene Modelle erscheinen als Provider **hub-local**.",
      "points": [
        "Ein Modell kann direkt von Hugging Face heruntergeladen werden; die Karte listet die `.gguf`-Dateien eines Repositorys mit Größe und Quantisierung, damit Sie eines passend zum Speicher wählen.",
        "Ein abgebrochener Download wird fortgesetzt statt neu zu beginnen.",
        "**Load** und **Unload** starten und stoppen ein Modell; es können nur begrenzt viele gleichzeitig laufen, das Laden eines weiteren verdrängt das zuletzt am wenigsten genutzte.",
        "Das Memory-Panel zeigt RAM und, wenn verfügbar, GPU-Speicher für jedes geladene Modell."
      ]
    },
    "s2": {
      "h": "Modellstruktur",
      "p": "Die Auswahl eines lokalen oder Katalog-Modells öffnet eine reine Leseansicht seiner Struktur, gelesen aus dem Header der Modelldatei ohne die Gewichte zu laden: Architektur, Anzahl der Schichten, Tensortypen und ein geschätzter Speicherbedarf, dargestellt als Blockdiagramm oder 3D-Stapel.",
      "points": [
        "Ein nur über eine API erreichbares Modell zeigt stattdessen eine einfachere Karte mit Kontextfenster, Preisen und Veröffentlichungsdatum aus dem Katalog.",
        "Ein aufgeteiltes Modell (mehrere `.gguf`-Teile) wird gelesen und als ein Modell dargestellt."
      ]
    },
    "s3": {
      "h": "Der Hub als Provider",
      "p": "Der Hub stellt eine OpenAI-kompatible API unter `/v1` bereit, sodass jeder Client, der die OpenAI Chat Completions API spricht, jedes im Katalog aktivierte Modell über eine Adresse und einen Zugangsschlüssel aufrufen kann.",
      "points": [
        "`GET /v1/models` listet jedes aktivierte Modell sowie jeden Agenten als `agent:<agent_id>`, der selbst als Modell nutzbar ist.",
        "Eine Completion mit einem Agenten-Modell führt den Agenten durch die normale Chat-Pipeline aus, mit seinen eigenen Tools, seinem Speicher und Budget, und liefert die Run-ID zusammen mit der Antwort.",
        "Ein tägliches Token-Limit pro Aufrufer kann gesetzt werden; darüber werden Aufrufe abgelehnt, bevor ein Modell überhaupt läuft."
      ]
    },
    "callout": "Eine lokale Modelldatei kann nicht gelöscht werden, solange sie geladen ist: zuerst Unload ausführen."
  },
  "health": {
    "nav": "Health",
    "title": "Health, das Runbook und der System-Workspace",
    "lead": "Die Seite [Status](/health) zeigt, ob die beweglichen Teile am Leben sind, und der Doctor beurteilt, ob dieser Zustand tatsächlich in Ordnung ist. Zwei Service Level Objectives verfolgen die Startzeit von Runs und die Fehlerrate, und ein System-Workspace beobachtet die eigene Diagnose des Dienstes und schlägt Korrekturen als Branches vor, ohne je selbst zu pushen.",
    "s0": {
      "h": "Die Momentaufnahme und der Doctor",
      "points": [
        "`GET /api/health` meldet die Datenbank, Run-Zahlen, Hintergrunddienste, Speichergröße und welche Provider-Schlüssel gesetzt sind; `null` bedeutet, die Prüfung konnte es nicht feststellen, nicht dass etwas ausgefallen ist.",
        "Der Doctor macht aus dieser Momentaufnahme Urteile: jede Prüfung liefert `ok`, `warn`, `fail` oder `skip` mit einem Satz und einer Lösung, der Gesamtstatus ist der schlechteste davon.",
        "Zu den Prüfungen gehören Migrationen, der Standard-Provider, CORS, veraltete Runs, die Run-Warteschlange, freier Speicherplatz, der Browser-Dienst, die Modell-Laufzeit, Docker, die Sandbox-Provider und der System-Workspace selbst."
      ]
    },
    "s1": {
      "h": "Das Runbook",
      "p": "Für einen Fehler, den ein Betreiber tatsächlich erlebt (ein ungültiger Provider-Schlüssel, ein nicht aktiviertes Modell, eine festhängende Run-Warteschlange, ein voller Datenträger, eine festhängende Lease, ein Proxy, der den Live-Stream puffert), gibt das Runbook das Symptom, wie man es mit dem Doctor oder der Health-Momentaufnahme bestätigt, und die Lösung. Die meisten Prüfungen erfolgen, bevor etwas läuft, sodass eine Ablehnung, etwa ein Budgetlimit oder ein deaktiviertes Modell, nie einen halb ausgeführten Run hinterlässt."
    },
    "s2": {
      "h": "SLOs und das Support-Paket",
      "points": [
        "Zwei Ziele werden über ein gleitendes Zeitfenster beurteilt: das 95. Perzentil der Run-Startzeit und der Anteil abgeschlossener Runs, die fehlgeschlagen sind.",
        "Die Karte **SLO** auf der Health-Seite zeigt beide als ok, breach oder no_data (zu wenige Runs zur Beurteilung).",
        "**Download support bundle** sammelt das Doctor-Ergebnis, die Health-Momentaufnahme, jüngste Fehler und den SLO-Status, mit allem, was wie ein Schlüssel oder Token aussieht, geschwärzt, bereit zum Anhängen bei einer Support-Anfrage."
      ]
    },
    "s3": {
      "h": "Der System-Workspace",
      "p": "Ein Workspace namens **system**, in dem der Dienst sich selbst betreut: ein geplanter Loop liest die eigene Diagnose, legt Befunde als Aufgaben an und schlägt Korrekturen als Commits auf einem Branch einer privaten Git-Kopie des Repositorys vor.",
      "points": [
        "Der Loop pusht nie: seine Agenten besitzen kein Tool, das pushen oder Daten nach außen senden kann, auf jeder Ebene durchgesetzt, ohne Ausnahme.",
        "Eine Korrektur landet als Commit auf einem Branch `system/<Datum>-<Aufgabe>`: Sie holen ihn selbst, prüfen ihn und pushen ihn selbst.",
        "Der Loop wird pausiert ausgeliefert; aktiviert wird er über den System-Bereich der Health-Seite."
      ]
    },
    "callout": "Eine hohe Zahl bei `running_runs` ohne lebende Instanzen bedeutet meist verwaiste Datensätze eines abgestürzten Prozesses, nicht tatsächlich laufende Arbeit."
  },
  "production": {
    "nav": "Produktion",
    "title": "Betrieb in Produktion",
    "lead": "So betreiben Sie den Hub mit mehr als einem Backend: Postgres statt SQLite, die Rollen `api` und `worker`, ein Objektspeicher für gemeinsame Dateien sowie sichere Backups, Releases und Rollbacks.",
    "s0": {
      "h": "Rollen und die Startwarteschlange",
      "p": "`AGENTS_HUB_ROLE=api` bedient die API und stellt jeden Lauf in eine Warteschlange, statt ihn selbst zu starten; `ah worker` übernimmt auf einem beliebigen Host einen Start aus dieser Warteschlange und führt ihn dort aus.",
      "points": [
        "Läufe melden sich über einen Heartbeat statt über eine PID, sodass ein abstürzender Worker den bereits gestarteten Lauf nicht stoppt.",
        "Ein toter Lauf mit einem Checkpoint wird automatisch davon neu gestartet, bis zu einer festen Grenze, statt sofort fehlzuschlagen.",
        "Hintergrundaufgaben, die nur einmal laufen dürfen, etwa der Scheduler und der Watchdog, laufen unter einer Datenbanksperre, damit mehrere Replikate sie nie doppelt ausführen."
      ]
    },
    "s1": {
      "h": "Postgres und die Broker-Bridge",
      "p": "Standardmäßig läuft ein Backend mit SQLite auf der Festplatte. Für mehrere Backends braucht es `AGENTS_HUB_DATABASE_URL` auf Postgres, damit sich alle Replikate eine Datenbank teilen, und `AGENTS_HUB_BROKER_URL` auf Redis, damit ein auf einem Replikat beendeter Lauf auch einen Browser-Tab erreicht, der mit einem anderen Replikat verbunden ist.",
      "points": [
        "`docker compose --profile ha` startet mit einem Befehl Postgres, Redis, einen mitgelieferten Objektspeicher, zwei API-Replikate und zwei Worker.",
        "Das Helm-Chart in `deploy/helm/agents-hub` gibt einem echten Cluster dieselbe Struktur, ohne Postgres oder Redis selbst zu betreiben."
      ]
    },
    "s2": {
      "h": "Die Cluster-Seite",
      "p": "Auf der Seite [Cluster](/cluster) zeigt sich eine Bereitstellung über mehrere Hosts: jedes Backend und jeder Worker, die Startwarteschlange, aktive Läufe nach Host, Dienste mit ihren Replikaten und das Protokoll jedes Mitglieds.",
      "points": [
        "Ein Mitglied gilt als aktiv, als veraltet, wenn sein Heartbeat seit etwa einer Minute nicht erneuert wurde, oder als gestoppt nach einem geordneten Herunterfahren.",
        "Jeder Dienst zeigt die Zahl seiner aktiven Replikate im Verhältnis zu Minimum und Maximum."
      ]
    },
    "s3": {
      "h": "Objektspeicher für gemeinsame Dateien",
      "p": "Lauf-Protokolle, Workspaces und View-Dateien liegen weiterhin als Dateien auf der Festplatte, nicht als Datenbankzeilen. Wird `AGENTS_HUB_BLOB_URL` auf einen S3-kompatiblen Bucket gesetzt, werden Lauf-Protokolle, Flow-Protokolle und View-Dateien dorthin gespiegelt, sodass ein Replikat eine Datei auch lesen kann, die es nicht selbst geschrieben hat.",
      "points": [
        "Workspaces selbst brauchen weiterhin einen gemeinsamen Mount, den der Objektspeicher noch nicht abdeckt.",
        "Bleibt die Einstellung leer, bleibt alles lokal, genau wie bei einer Bereitstellung mit nur einem Replikat."
      ]
    },
    "s4": {
      "h": "Backups, Releases und Rollback",
      "p": "`ah db backup` schreibt ein Archiv mit der Datenbank und dem Zustand daneben; `ah db restore` spielt es zurück, und `ah db verify` prüft es, ohne die laufende Datenbank anzufassen. Ein Release ist eine Version, die für Backend, Dashboard und Helm-Chart gilt, geschnitten mit `scripts/release.py`.",
      "points": [
        "Erstellen Sie vor jedem Upgrade ein Backup: Ein neuerer Build migriert die Datenbank in dem Moment, in dem er sie öffnet.",
        "Ein älterer Build weigert sich, eine bereits von einem neueren migrierte Datenbank zu öffnen, daher bedeutet ein Rollback eines Releases mit Migrationen eine Wiederherstellung aus dem Backup, nicht nur ein Auschecken des alten Codes."
      ]
    },
    "callout": "Wird `docker compose --scale backend=N` ohne gesetztes `AGENTS_HUB_BROKER_URL` ausgeführt, erhalten Browser-Tabs auf anderen Replikaten keine Live-Updates mehr, bis sie neu laden."
  },
  "accounts": {
    "nav": "Nutzer und Zugriff",
    "title": "Nutzer und Zugriff",
    "lead": "Wer sich anmelden kann, was er darf und wie der Hub sich das merkt: Anmeldemodi, Single Sign-on, SCIM-Provisionierung, persönliche API-Schlüssel, Secrets für Agenten und das Audit-Protokoll.",
    "s0": {
      "h": "Anmeldemodi",
      "p": "`AUTH_MODE` bestimmt, wie Personen den Hub erreichen: `single` für eine einzelne Bedienperson ganz ohne Anmeldung, `token` für ein gemeinsames Secret, `multi` für benannte Konten mit Rollen. Der Wert steht in `.env` und braucht zum Ändern einen Neustart des Backends.",
      "points": [
        "`single` passt für einen Laptop oder einen Host, den nur Sie erreichen; `token` ist die Untergrenze, sobald der Port für andere erreichbar ist; `multi` ist der einzige Modus, der Personen auseinanderhält.",
        "Der geltende Modus wird auf der Seite [Einstellungen](/settings) unter API-Zugriff angezeigt."
      ]
    },
    "s1": {
      "h": "Konten, Rollen und Single Sign-on",
      "p": "Im Modus `multi` hat jedes Konto eine globale Rolle, `admin` oder `member`, sowie eine Rolle in jedem Workspace, dem es angehört: `owner`, `editor` oder `viewer`, alles verwaltet auf der Seite [Konten](/users). Single Sign-on lässt Personen sich mit Keycloak, Microsoft Entra ID, Google Workspace oder einem anderen OpenID-Connect-Anbieter anmelden, und eine Gruppenzuordnung kann eine Gruppe des Anbieters automatisch in eine Rolle verwandeln.",
      "points": [
        "Das erste Konto wird einmalig über ein Bootstrap-Formular angelegt, das erscheint, bevor sich jemand anders anmelden kann.",
        "Eine über eine Gruppenzuordnung vergebene Mitgliedschaft wird als über Gruppe gekennzeichnet, ihre Rolle lässt sich dort nicht von Hand ändern: Ändern Sie stattdessen die Zuordnung."
      ]
    },
    "s2": {
      "h": "SCIM-Provisionierung",
      "p": "Ein Identitätsanbieter wie Entra ID, Okta oder Keycloak kann Konten direkt über `/scim/v2` anlegen, umbenennen und deaktivieren, in dem Moment, in dem sie sich in seinem eigenen Verzeichnis ändern, statt dass jemand diese Änderung hier von Hand nachvollzieht.",
      "points": [
        "Wird durch Setzen von `AUTH_SCIM_TOKEN` aktiviert.",
        "Deaktiviert der Anbieter eine Person, werden sofort alle offenen Sitzungen beendet und alle persönlichen API-Schlüssel widerrufen, nicht erst beim nächsten Ablauf."
      ]
    },
    "s3": {
      "h": "Persönliche API-Schlüssel und Secrets",
      "p": "Auf der Seite [Konto](/account) legt eine angemeldete Person eigene API-Schlüssel an, für die CLI, einen CI-Job oder einen anderen Hub, wahlweise auf eine Liste von Workspaces eingeschränkt, mit eigenen Raten- und Kostenlimits. Ein Workspace kann außerdem verschlüsselte Secrets vorhalten, die nur sein Owner verwalten darf und die einem Lauf nur unter dem Namen übergeben werden, den dessen Agent deklariert.",
      "points": [
        "Ein Schlüssel handelt als sein Owner und reicht nie weiter; er funktioniert nicht mehr, sobald er widerrufen wird oder sein Owner deaktiviert ist.",
        "Ein Agent erhält ein Secret nur, wenn seine Definition den Namen aufführt; treffen mehrere Bereiche zu, gewinnt der genaueste: Agent und Nutzer, dann Agent, dann Nutzer, dann der ganze Workspace."
      ]
    },
    "s4": {
      "h": "Das Audit-Protokoll",
      "p": "Die Seite [Audit](/audit) zeichnet auf, wer was getan hat: Anmeldungen, Änderungen an Rollen und Mitgliedschaften, gestartete Läufe, genehmigte Werkzeugaufrufe und Änderungen an der Workspace-Richtlinie, exportierbar als CSV oder JSONL.",
      "points": [
        "Ein Administrator sieht jede Zeile; alle anderen sehen nur ihre eigenen Aktionen sowie die Zeilen eines Workspace, den sie besitzen.",
        "Wird in den Modi `token` und `multi` geführt; im Modus `single` gibt es nur eine Bedienperson und nichts zu prüfen."
      ]
    },
    "callout": "401 bedeutet, der Hub kennt Sie nicht und führt Sie zum Anmeldebildschirm zurück; 403 bedeutet, er kennt Sie, verweigert aber genau diese Aktion, ohne Ihre Sitzung anzutasten."
  },
  "widget": {
    "nav": "Chat-Widget",
    "title": "Einbettbares Chat-Widget",
    "lead": "Eine Chat-Blase für Ihre eigene Website: ein einziger Script-Tag gibt Besuchern einen Chat mit einem Agenten eines Workspace, mit live gestreamten Antworten und Dateianhängen von ihrem eigenen Rechner. Angelegt, eingebettet und eingesehen auf der Seite [Widgets](/widgets).",
    "s0": {
      "h": "Erstellen und einbetten",
      "p": "Ein Widget ist an einen Workspace und einen Agenten gebunden; sein Embed-Tab liefert den genauen Script-Tag zum Einfügen vor dem schließenden body-Tag einer Seite.",
      "points": [
        "Listen Sie die genauen Origins auf, auf denen das Widget laufen darf: Ein Browser verweigert die Einbettung überall sonst.",
        "Das Rotieren des publizierbaren Schlüssels schaltet jede Kopie eines alten Snippets sofort ab, ohne die Unterhaltungen der Besucher zu verlieren."
      ]
    },
    "s1": {
      "h": "Was ein Besucher sieht",
      "p": "Ein Besucher bekommt eine Chat-Blase mit gestreamten Antworten, nummerierten Quellenangaben und Dateianhängen, gezeichnet in einem abgeschlossenen Teil der Seite, den das eigene CSS der Website nicht durchbrechen kann.",
      "points": [
        "Die Blase zeigt nie Werkzeug-Eingaben, das Nachdenken des Agenten oder welcher Workspace und welcher Agent antwortet.",
        "Grenzen für Nachrichten pro Minute, Anhangsgröße und Tokens pro Tag schützen das Widget vor Überlastung."
      ]
    },
    "s2": {
      "h": "Unterhaltungen und Vorschau",
      "p": "Der Tab Conversations listet die Threads jedes Besuchers mit einem Link zum Lauf hinter jeder Antwort. Der Tab Preview probiert das Widget live gegen das echte Script aus, noch bevor es eingeschaltet ist.",
      "points": [
        "Das Löschen eines Threads ist endgültig; die Läufe dahinter bleiben auf der Seite [Läufe](/messages) erhalten."
      ]
    },
    "callout": "Ein Widget antwortet als die Person, die es angelegt hat: Verlässt diese Person den Workspace, funktioniert das Widget nicht mehr, bis jemand anderes zugewiesen wird."
  },
  "integrations": {
    "nav": "Integrationen",
    "title": "Benachrichtigungen und Integrationen",
    "lead": "Wie der Hub mit der Außenwelt spricht: signierte Webhooks und Slack-Nachrichten, ein Telegram-Bot, Pull Requests über eine GitHub App, andere Agenten über A2A und reine Lesezugriffe von Agenten, die schon anderswo laufen.",
    "s0": {
      "h": "Webhooks, Slack und Alarmregeln",
      "p": "Auf der Seite [Konnektoren](/connectors) erhält ein Endpunkt einen signierten Webhook oder eine Slack-Nachricht, sobald der Posteingang eine Benachrichtigung auslöst, eine Alarmregel greift oder das Audit-Protokoll eine Zeile aufzeichnet. Eine Alarmregel kann selbst eine Benachrichtigung auslösen: für einen fehlgeschlagenen Lauf, Kosten eines Laufs oder eines Tages über einem Schwellenwert, oder ein Online-Eval-Ergebnis unter dem Zielwert.",
      "points": [
        "Jede Webhook-Zustellung ist mit dem Secret des Endpunkts signiert; prüfen Sie den Header `X-AgentsHub-Signature`, bevor Sie ihr vertrauen.",
        "Eine fehlgeschlagene Zustellung wird einmal wiederholt; ein Endpunkt, der die Nutzlast mit einer beliebigen Nicht-5xx-Antwort ablehnt, gilt als zugestellt."
      ]
    },
    "s1": {
      "h": "Telegram",
      "p": "Die Bindung eines Telegram-Chats an einen Workspace und einen Agenten lässt eine Unterhaltung vom Telefon aus weiterlaufen, und Benachrichtigungen können ebenfalls dorthin gesendet werden.",
      "points": [
        "Eine Nachricht aus Telegram gilt für den Capability Guard als nicht vertrauenswürdige Eingabe, da jeder, der den Bot erreicht, dem Agenten eigene Worte in den Kontext legen kann."
      ]
    },
    "s2": {
      "h": "Die GitHub App",
      "p": "Ist eine GitHub App auf der Seite [Konnektoren](/connectors) eingerichtet, stellt der Hub seine eigenen kurzlebigen GitHub-Tokens aus: ein Installationstoken pro Workspace, oder das eigene Token einer Person, sobald sie ihr Konto auf der Seite [Konto](/account) verbindet. Pull Requests stammen dann vom Bot der App oder von dieser Person, statt von einem einzigen langlebigen Token, das in den Einstellungen hinterlegt ist.",
      "points": [
        "Ein Agent erhält `GITHUB_TOKEN` nur, wenn er den Namen deklariert, genau wie jedes andere Secret.",
        "Ein Workspace hält jeweils eine Installation; das Binden einer weiteren ersetzt die vorherige."
      ]
    },
    "s3": {
      "h": "A2A und Verbindungen",
      "p": "Jeder Agent veröffentlicht eine A2A-Agent-Card, damit ein externer Orchestrator ihn über JSON-RPC aufrufen kann, ohne diese API zu kennen, und ein Agent, der anderswo läuft, lässt sich allein anhand der URL seiner Card importieren. Eine [Verbindung](/connections) ist die umgekehrte Richtung: Ein Agent, der bereits über eigene Trigger läuft, meldet dem Hub, was er tut, und der Hub schaut nur zu.",
      "points": [
        "Ein A2A-Aufruf wird zu einem gewöhnlichen Task und Lauf des Hubs, mit derselben Kostenberechnung und Protokollierung wie jeder andere.",
        "Das Token einer Verbindung erreicht nur die Ingest-Endpunkte; es kann weder das Dashboard lesen noch Tasks anlegen noch auf den Speicher zugreifen."
      ]
    },
    "callout": "Ein eingehender Webhook, der einen Task anlegt, hat keinen offenen Fallback: Ist für einen Workspace kein eingehendes Secret konfiguriert, lässt er sich überhaupt nicht ansprechen."
  }
};
