// Guide sections of the Documentation page (pages/Docs.jsx, GuideDoc):
// docsGuide.<key>.nav / title / lead / s0..s5 { h, p?, points? } / callout?.
// Written from the corpus in /docs; each section links its corpus files as
// the full reference, so facts belong there first.
export default {
  "files": {
    "nav": "Dateien",
    "title": "Dateien des Arbeitsbereichs",
    "lead": "Eine Datei des Arbeitsbereichs wird einmal gespeichert und überall über ihre id verwendet: in einer Chat-Nachricht, in einem Speicher-Pool, in einer Aufgabe oder in dem, was ein Agent selbst gespeichert hat. Laden Sie sie einmal auf der Seite [Dateien](/files) hoch, und all diese Stellen finden dieselbe Datei.",
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
        "Aufgaben: Dateien, die einer Aufgabe angehängt sind, werden bei jedem Lauf in ihr Arbeitsverzeichnis kopiert.",
        "Agenten: Ein Agent kann Dateien des Arbeitsbereichs mit eigenen Werkzeugen auflisten, lesen und speichern, und eine Chat-Antwort verlinkt, was er erzeugt hat."
      ]
    },
    "s3": {
      "h": "Zitate",
      "p": "Wenn ein Agent den Speicher durchsucht, kommen die verwendeten Abschnitte und Notizen nummeriert als `[n]` zurück, und die Antwort zeigt sie als Quellen unter dem Text. Ein Klick auf eine Quelle öffnet die dahinterliegende Datei des Arbeitsbereichs, oder, wenn es keine gibt, die Speicherseite."
    },
    "callout": "Dateien, die außerhalb des üblichen Wegs entstehen, etwa durch Claude Code, Codex oder eine Shell, werden nicht automatisch gelistet: nutzen Sie **Aus Ordner synchronisieren** auf der Seite Dateien, um sie zu registrieren."
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
        "Fallback-Modelle wiederholen einen fehlgeschlagenen Aufruf beim nächsten Modell in der Liste, mit denselben Tools.",
        "Ein Beratermodell, gewählt auf dem Tab **Modell** des Agenten, beantwortet bei schwierigen Schritten Fragen über `consult_advisor`; es sieht nur, was der Agent in den Aufruf schreibt, und seine Kosten zählen zum Run.",
        "Ein Werkzeugergebnis über der Auslagerungsgrenze des Workspace wird als Datei unter `tool-outputs/` gespeichert; das Modell sieht Anfang, Ende und den Pfad und liest den Rest mit `read_file`."
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
        "**Anweisung** fügt Ihren Text für den Rest des Runs dem Systemprompt des Agenten hinzu. Nur der Besitzer des Runs oder ein Admin darf sie senden, nie ein Agent.",
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
        "Manche Seiten, etwa eine Loop, ein Team, die Definition eines Agenten oder ein Memory-Pool, haben ihren eigenen Chat mit eigenem Agenten; das Panel zeigt nur dieses Gespräch.",
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
    "nav": "Ergebnisbewertung",
    "title": "Ergebnisbewertung (Outcomes)",
    "lead": "Ein Outcome bewertet das Ergebnis einer Task anhand einer Rubrik und kann sie für einen weiteren Versuch zurückschicken.",
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
    }
  },
  "sessionsRuns": {
    "nav": "Sessions & Runs",
    "title": "Sessions und Runs",
    "lead": "Ein Run ist ein einzelner Agentenaufruf, die kleinste Einheit, die das Dashboard zeigt. Sessions und Run-Gruppen zeigen, wie Runs zusammengehören.",
    "s0": {
      "h": "Runs",
      "p": "Jede Chat-Nachricht, jeder Flow-Knoten und jede Delegation ist ein Run.",
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
        "Vier Arten besitzen jeweils eine Reihe von Runs: Flow, Loop, Team und Container.",
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
  "deployments": {
    "nav": "Deployments",
    "title": "Deployments, Environments und Sandboxes",
    "lead": "Die Seite **Deployments** ist eine Arbeitsansicht über die geplanten Jobs, die tatsächlich produktive Arbeit leisten: agent-, flow- und loop-Jobs von der Seite [Plan](/plan), jeder mit einem Protokoll jeder Auslösung.",
    "s0": {
      "h": "Was ein Deployment hinzufügt",
      "points": [
        "**auto_pause_after**: nach wie vielen aufeinanderfolgenden fehlgeschlagenen Auslösungen der Job automatisch pausiert wird, `0` schaltet dies ab, Standard `3`.",
        "**agent_version** (nur bei Agent-Jobs): den Job auf eine gespeicherte Agentenversion festlegen."
      ]
    },
    "s1": {
      "h": "Das Auslösungsprotokoll",
      "p": "Jeder Auslöseversuch wird erfasst, erfolgreich oder nicht: wann er stattfand, ob er automatisch war oder ein manuelles **Run now**, und bei Fehlschlag ein Fehlertyp wie `agent_missing` oder `capacity`. Die Detailansicht eines Deployments zeigt dieses Protokoll mit einem Nur-Fehler-Filter.",
      "points": [
        "Ein Job, der ständig fehlschlägt, pausiert sich selbst; existiert das Ziel (Agent, Flow oder Loop) nicht mehr, pausiert er sofort.",
        "**Run now** löst einen Job sofort aus, auch pausiert, damit eine Korrektur getestet werden kann, ohne ihn vorher fortzusetzen."
      ]
    }
  },
  "health": {
    "nav": "Health",
    "title": "Health, das Runbook und der System-Workspace",
    "lead": "Die Seite [Status](/health) zeigt, ob die beweglichen Teile am Leben sind, und der Doctor beurteilt, ob dieser Zustand tatsächlich in Ordnung ist. Ein System-Workspace beobachtet die eigene Diagnose des Dienstes und schlägt Korrekturen als Branches vor, ohne je selbst zu pushen.",
    "s0": {
      "h": "Die Momentaufnahme und der Doctor",
      "points": [
        "`GET /api/health` meldet die Datenbank, Run-Zahlen, Hintergrunddienste, Speichergröße und welche Provider-Schlüssel gesetzt sind; `null` bedeutet, die Prüfung konnte es nicht feststellen, nicht dass etwas ausgefallen ist.",
        "Der Doctor macht aus dieser Momentaufnahme Urteile: jede Prüfung liefert `ok`, `warn`, `fail` oder `skip` mit einem Satz und einer Lösung, der Gesamtstatus ist der schlechteste davon.",
        "Zu den Prüfungen gehören Migrationen, der Standard-Provider, CORS, veraltete Runs, die Run-Warteschlange, freier Speicherplatz, Docker, die Sandbox-Provider und der System-Workspace selbst."
      ]
    },
    "s1": {
      "h": "Das Runbook",
      "p": "Für einen Fehler, den ein Betreiber tatsächlich erlebt (ein ungültiger Provider-Schlüssel, ein nicht aktiviertes Modell, eine festhängende Run-Warteschlange, ein voller Datenträger, eine festhängende Lease, ein Proxy, der den Live-Stream puffert), gibt das Runbook das Symptom, wie man es mit dem Doctor oder der Health-Momentaufnahme bestätigt, und die Lösung. Die meisten Prüfungen erfolgen, bevor etwas läuft, sodass eine Ablehnung, etwa ein Budgetlimit oder ein deaktiviertes Modell, nie einen halb ausgeführten Run hinterlässt."
    },
    "s2": {
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
      "h": "Objektspeicher für gemeinsame Dateien",
      "p": "Lauf-Protokolle, Workspaces und View-Dateien liegen weiterhin als Dateien auf der Festplatte, nicht als Datenbankzeilen. Wird `AGENTS_HUB_BLOB_URL` auf einen S3-kompatiblen Bucket gesetzt, werden Lauf-Protokolle, Flow-Protokolle und View-Dateien dorthin gespiegelt, sodass ein Replikat eine Datei auch lesen kann, die es nicht selbst geschrieben hat.",
      "points": [
        "Workspaces selbst brauchen weiterhin einen gemeinsamen Mount, den der Objektspeicher noch nicht abdeckt.",
        "Bleibt die Einstellung leer, bleibt alles lokal, genau wie bei einer Bereitstellung mit nur einem Replikat."
      ]
    },
    "s3": {
      "h": "Backups, Releases und Rollback",
      "p": "`ah db backup` schreibt ein Archiv mit der Datenbank und dem Zustand daneben; `ah db restore` spielt es zurück, und `ah db verify` prüft es, ohne die laufende Datenbank anzufassen. Ein Release ist eine Version, die für Backend, Dashboard und Helm-Chart gilt, geschnitten mit `scripts/release.py`.",
      "points": [
        "Erstellen Sie vor jedem Upgrade ein Backup: Ein neuerer Build migriert die Datenbank in dem Moment, in dem er sie öffnet.",
        "Ein älterer Build weigert sich, eine bereits von einem neueren migrierte Datenbank zu öffnen, daher bedeutet ein Rollback eines Releases mit Migrationen eine Wiederherstellung aus dem Backup, nicht nur ein Auschecken des alten Codes."
      ]
    },
    "callout": "Wird `docker compose --scale backend=N` ohne gesetztes `AGENTS_HUB_BROKER_URL` ausgeführt, erhalten Browser-Tabs auf anderen Replikaten keine Live-Updates mehr, bis sie neu laden."
  },
  "integrations": {
    "nav": "Integrationen",
    "title": "Benachrichtigungen und Alarmregeln",
    "lead": "Wie der Hub Ihnen sagt, dass etwas passiert ist: der Posteingang unter der Glocke und Alarmregeln, die selbst Benachrichtigungen auslösen.",
    "s0": {
      "h": "Alarmregeln",
      "p": "Auf der Seite [Konnektoren](/connectors) schaltet der Tab Alarmregeln eine Regel scharf, die selbst eine Benachrichtigung auslöst: für einen fehlgeschlagenen Lauf oder Kosten eines Laufs oder eines Tages über einem Schwellenwert. Jede Benachrichtigung landet im Posteingang unter der Glocke.",
      "points": [
        "Eine Regel lässt sich auf einen Agenten beschränken und abschalten, ohne sie zu löschen."
      ]
    },
    "callout": "Agenten schicken Ihnen mit notify_user eine Benachrichtigung, und eine geplante Benachrichtigung auf der Seite [Plan](/plan) landet im selben Posteingang."
  },
  "assistant": {
    "nav": "Assistent",
    "title": "Ihr Assistent",
    "lead": "Ein Assistent für den ganzen Dienst, per Sprache oder Text, auf der Seite [Assistent](/assistant). Er beantwortet Fragen zum Hub, nimmt kleine Änderungen nach Ihrer Bestätigung auf einer Karte vor und führt nach der Installation durch die Einrichtung.",
    "s0": {
      "h": "Ein Gespräch pro Person",
      "p": "Jede Person hat ihr eigenes Gespräch. Ein Zug läuft in einem Workspace, zu dem die Person gehört, und ein Gespräch bleibt in dem Workspace, in dem es begonnen hat.",
      "points": [
        "Der Assistent erweitert den Main Agent: dieselben Werkzeuge und dazu eigene Abfragen.",
        "Administratoren erhalten zusätzlich ein Dienstgespräch mit Werkzeugen für den Zustand des Dienstes."
      ]
    },
    "s1": {
      "h": "Was er nachschlagen und ändern kann",
      "p": "`hub_lookup` liest Runs, Sitzungen, Ausgaben, Budgets, Modelle, Agenten, Benachrichtigungen, Freigaben und die Einträge jeder Seite. `hub_action` führt eine Aktion an einem Eintrag aus, etwa eine Instanz pausieren, und zeigt immer zuerst eine Karte.",
      "points": [
        "Ein Satz wie „jeden Morgen um 8 eine Zusammenfassung schicken“ wird nach Ihrem Ja zu einem geplanten Job.",
        "Schlüssel eines Anbieters geben Sie selbst in eine Karte ein, nie in das Gespräch."
      ]
    },
    "s2": {
      "h": "Sprache",
      "p": "Zum Sprechen drücken, Gesprächsmodus oder ein Weckwort. Sprache läuft über die Erkennungs- und Sprachmodelle des Workspace, sonst über die des Browsers. Ein gesprochenes Ja beantwortet eine wartende Karte.",
      "points": [
        "Sprache und die Installation der App auf dem Telefon brauchen HTTPS."
      ]
    },
    "s3": {
      "h": "Limits",
      "p": "Jeder Zug ist ein Run mit dem Namen der Person und zählt zu ihrem Monatslimit. Ist ein Limit oder das Budget des Workspace aufgebraucht, wird der Zug mit einer Karte abgelehnt, die sagt, welches."
    }
  }
};
